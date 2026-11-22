"""本地有限并发下单实验；通过真实接口下单/关单，SQL仅准备独立购物车及对账。

依赖 PyMySQL。密码交互输入，报告不记录密码、token 或数据库凭据。
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import getpass
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pymysql


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='实际创建测试购物车并发送请求；默认仅预检')
    parser.add_argument('--requests', type=int, default=10)
    parser.add_argument('--quantity', type=int, default=1)
    parser.add_argument('--sku-id', type=int, default=245)
    parser.add_argument('--address-id', type=int, help='默认选择测试会员的首个收货地址')
    parser.add_argument('--username', default='demo')
    parser.add_argument('--db-port', type=int, default=int(os.environ.get('MALL_DB_PORT', '13306')))
    parser.add_argument('--db-user', default=os.environ.get('MALL_DB_USER', 'mall'))
    parser.add_argument('--base-url', default='http://localhost:8085')
    args = parser.parse_args()
    if not 2 <= args.requests <= 20 or args.quantity < 1:
        parser.error('请求数必须在2～20之间，购买数量必须为正数')

    repo = Path(__file__).resolve().parents[2]
    if args.db_port not in (3306, 13306):
        parser.error('仅允许本机演示数据库端口 3306 / 13306')
    if args.base_url.rstrip('/') not in ('http://localhost:8085', 'http://localhost:18085',
                                         'http://127.0.0.1:8085', 'http://127.0.0.1:18085'):
        parser.error('仅允许本机商城演示接口')
    db_password = os.environ.get('MALL_DB_PASSWORD')
    if db_password is None:
        db_password = getpass.getpass('演示数据库密码（Compose 默认 mall-local-only）: ')
    conn = pymysql.connect(host='localhost', port=args.db_port, user=args.db_user,
                           password=db_password, database='mall', charset='utf8mb4',
                           cursorclass=pymysql.cursors.DictCursor, connect_timeout=5)
    del db_password
    base = args.base_url.rstrip('/')
    report = {'run_id': 'learn-concurrency-' + uuid.uuid4().hex[:12],
              'started_at': datetime.now().isoformat(), 'requests': args.requests,
              'concurrent_workers': args.requests, 'quantity_per_request': args.quantity,
              'sku_id': args.sku_id, 'scope': '本地有限并发正确性实验，不用于性能压测'}
    output = repo / 'document/verification/results' / (report['run_id'] + '.json')
    token = None

    def query(sql, values=()):
        # 避免 REPEATABLE READ 下复用同一个旧快照。
        conn.rollback()
        with conn.cursor() as cursor:
            cursor.execute(sql, values)
            return cursor.fetchall()

    def stock():
        rows = query('SELECT id, stock, lock_stock, stock-lock_stock AS available FROM pms_sku_stock WHERE id=%s', (args.sku_id,))
        if len(rows) != 1:
            raise RuntimeError('SKU不存在')
        return rows[0]

    def api(path, body=None, form=None, method='POST'):
        headers = {}
        if token:
            headers['Authorization'] = token
        if form is not None:
            payload = urllib.parse.urlencode(form).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        elif body is not None:
            payload = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        else:
            payload = None
        request = urllib.request.Request(base + path, data=payload, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=40)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    cart_ids = []
    try:
        member = query('SELECT id,username FROM ums_member WHERE username=%s AND status=1', (args.username,))
        if len(member) != 1:
            raise RuntimeError('测试会员不存在或未启用')
        member_id = member[0]['id']
        if args.address_id is None:
            addresses = query('SELECT id FROM ums_member_receive_address WHERE member_id=%s ORDER BY default_status DESC,id LIMIT 1', (member_id,))
            if not addresses:
                raise RuntimeError('请先为测试会员添加收货地址')
            args.address_id = addresses[0]['id']
        if not query('SELECT id FROM ums_member_receive_address WHERE id=%s AND member_id=%s', (args.address_id, member_id)):
            raise RuntimeError('收货地址不属于测试会员')
        sku = query('SELECT id,product_id,price,sku_code FROM pms_sku_stock WHERE id=%s', (args.sku_id,))[0]
        before = stock()
        if sku['price'] is None or sku['price'] <= 0 or before['available'] <= 0:
            raise RuntimeError('SKU价格或可用库存不符合实验条件')
        if args.requests * args.quantity <= before['available']:
            raise RuntimeError('请求总量必须超过可用库存，才能验证不足拒绝')
        report['before'] = before
        report['member_id'] = member_id
        report['expected_successes'] = before['available'] // args.quantity
        print('实验基线:', before)
        print(f"将发送{args.requests}个请求，每个购买{args.quantity}件；预期{report['expected_successes']}个成功。")
        if not args.run:
            print('当前仅预检，未创建购物车、未发送下单请求。使用 --run 启动实验。')
            return

        password = getpass.getpass('前台测试会员密码（输入不显示）: ')
        _, login = api('/sso/login', form={'username': args.username, 'password': password})
        del password
        if login.get('code') != 200:
            raise RuntimeError('测试会员登录失败')
        token = login['data']['tokenHead'] + login['data']['token']
        # 登录后再次确认基线，防止准备期间库存被其他实验修改。
        if stock() != before:
            raise RuntimeError('准备期间库存发生变化，请重新预检')
        with conn.cursor() as cursor:
            for index in range(args.requests):
                # /cart/add 会合并相同SKU，因此实验直接准备相互独立的购物车行。
                cursor.execute('INSERT INTO oms_cart_item (product_id,product_sku_id,member_id,quantity,price,product_name,product_sku_code,create_date,delete_status) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),0)',
                               (sku['product_id'], args.sku_id, member_id, args.quantity, sku['price'],
                                report['run_id'] + ':' + str(index + 1), sku['sku_code']))
                cart_ids.append(cursor.lastrowid)
        conn.commit()
        report['cart_ids'] = cart_ids
        save()  # 网络中断时仍可根据本轮标记查回订单。
        barrier = threading.Barrier(args.requests)

        def submit(index):
            barrier.wait(timeout=15)
            started = time.monotonic()
            result = {'request': index + 1, 'cart_id': cart_ids[index]}
            try:
                http, data = api('/order/generateOrder', body={
                    'memberReceiveAddressId': args.address_id, 'couponId': None,
                    'useIntegration': None, 'payType': 0, 'cartIds': [cart_ids[index]]})
                result.update(http_status=http, code=data.get('code'), message=data.get('message'))
                if data.get('code') == 200:
                    result['order_id'] = data['data']['order']['id']
            except Exception as error:
                # 不自动重发：超时不等于事务一定失败。
                result.update(error=type(error).__name__)
            result['elapsed_ms'] = round((time.monotonic() - started) * 1000)
            return result

        with ThreadPoolExecutor(max_workers=args.requests) as pool:
            results = list(pool.map(submit, range(args.requests)))
        report['responses'] = results
        after = stock()
        # 使用每个购物车写入订单明细的专属标记，避免按订单ID区间误计其他请求。
        orders = query('SELECT o.id,o.status,oi.product_sku_id,oi.product_quantity,oi.product_name FROM oms_order o JOIN oms_order_item oi ON oi.order_id=o.id WHERE o.member_id=%s AND oi.product_name LIKE %s ORDER BY o.id',
                       (member_id, report['run_id'] + ':%'))
        successes = [row for row in results if row.get('order_id') is not None]
        rejections = [row for row in results if row.get('code') != 200 and row.get('message') == '库存不足，无法下单']
        success_ids = {row['order_id'] for row in successes}
        committed_ids = {row['id'] for row in orders}
        checks = {
            'all_responses_classified': len(successes) + len(rejections) == args.requests,
            'success_count_matches_capacity': len(successes) == report['expected_successes'],
            'committed_orders_match_successes': committed_ids == success_ids and len(orders) == len(successes),
            'order_items_correct': all(row['status'] == 0 and row['product_sku_id'] == args.sku_id and row['product_quantity'] == args.quantity for row in orders),
            'physical_stock_unchanged': after['stock'] == before['stock'],
            'locked_stock_matches_orders': after['lock_stock'] - before['lock_stock'] == sum(row['product_quantity'] for row in orders),
            'stock_within_bounds': 0 <= after['lock_stock'] <= after['stock'],
        }
        report.update(after_requests=after, orders=orders, success_count=len(successes),
                      stock_rejections=len(rejections), other_errors=args.requests-len(successes)-len(rejections), checks=checks)
        save()
        print('下单结果:', json.dumps({key: report[key] for key in ['success_count','stock_rejections','other_errors']}, ensure_ascii=False))
        print('下单后库存:', after)
        print('对账:', checks)
        if not all(checks.values()):
            raise RuntimeError('存在未通过的对账项；保留本轮现场，不自动重发或清理订单，请先查看报告')

        # 对账通过后，通过业务接口关闭本轮订单，不直接改订单状态或库存。
        cleanup = []
        for order_id in sorted(success_ids):
            http, response = api('/order/cancelUserOrder', form={'orderId': order_id})
            cleanup.append({'order_id': order_id, 'http_status': http, 'code': response.get('code')})
        report['cleanup_responses'] = cleanup
        final = stock()
        final_orders = query('SELECT DISTINCT o.id,o.status FROM oms_order o JOIN oms_order_item oi ON oi.order_id=o.id WHERE o.member_id=%s AND oi.product_name LIKE %s ORDER BY o.id',
                             (member_id, report['run_id'] + ':%'))
        restored = final == before and all(row['status'] == 4 for row in final_orders)
        report.update(after_cleanup=final, final_orders=final_orders, cleanup_passed=restored)
        if restored:
            with conn.cursor() as cursor:
                cursor.execute('UPDATE oms_cart_item SET delete_status=1 WHERE member_id=%s AND product_name LIKE %s',
                               (member_id, report['run_id'] + ':%'))
            conn.commit()
        report['finished_at'] = datetime.now().isoformat()
        save()
        print('测试订单已关闭、库存恢复基线:', restored)
        print('报告:', output)
        if not restored:
            raise RuntimeError('收尾结果异常，请查看报告，不要手工清零库存')
    except Exception as error:
        conn.rollback()
        report['stopped_with_error'] = str(error)
        if cart_ids:
            save()
            print('实验现场和报告已保留:', output)
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
