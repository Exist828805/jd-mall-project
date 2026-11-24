"""本机 Docker 演示环境接口冒烟验收（Python 3 标准库）。

仅使用公开演示账号；创建并删除本轮浏览记录和上传文件，不操作订单库存。
容器健康状态、初始化 SQL、Redis 与 RabbitMQ 另由 Compose/容器命令核验。
"""
import argparse
import datetime
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='执行接口请求及本轮测试数据写入/清理')
    parser.add_argument('--output', type=Path, required=True, help='不含密码或 token 的 JSON 报告')
    args = parser.parse_args()
    if not args.run:
        parser.error('请仅在隔离演示环境中使用 --run 执行')
    admin, portal = 'http://127.0.0.1:18080', 'http://127.0.0.1:18085'
    report = {'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'scope': 'Docker 核心接口冒烟；不代表订单、支付或全量回归通过', 'checks': []}
    # Do not route local demo credentials through system proxy settings.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def raw(url, data=None, headers=None, method=None):
        request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        with opener.open(request, timeout=45) as response:
            assert response.status == 200, 'HTTP status is not 200'
            return response.read()

    def api(base, path, token=None, body=None, form=None):
        headers = {'Authorization': token} if token else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        result = json.loads(raw(base + path, data, headers))
        assert result.get('code') == 200, 'Business code is not 200'
        return result.get('data')

    def check(name, operation):
        try:
            detail = operation()
            report['checks'].append({'name': name, 'passed': True, 'detail': detail})
            print('PASS', name, flush=True)
            return detail
        except Exception as error:
            # Exceptions may contain URLs but never login request bodies or tokens.
            report['checks'].append({'name': name, 'passed': False, 'error': type(error).__name__})
            print('FAIL', name, type(error).__name__, flush=True)
            raise

    def document(base, group):
        raw(base + '/swagger-ui/index.html')
        spec = json.loads(raw(base + '/v3/api-docs/' + group))
        assert spec.get('paths'), 'OpenAPI paths are empty'
        return {'paths': len(spec['paths'])}

    def login(base, path, credentials, form=False):
        data = api(base, path, **({'form': credentials} if form else {'body': credentials}))
        assert data.get('token') and data.get('tokenHead'), 'Missing JWT'
        return data['tokenHead'] + data['token']

    def remove_test_object(name):
        # /minio/delete is protected by resource permissions absent from the seed SQL.
        # Clean up with the local storage administrator, without changing application RBAC.
        now = datetime.datetime.now(datetime.timezone.utc)
        date, stamp = now.strftime('%Y%m%d'), now.strftime('%Y%m%dT%H%M%SZ')
        path = urllib.parse.quote('/mall/' + name, safe='/~')
        digest = hashlib.sha256(b'').hexdigest()
        canonical_headers = ('host:127.0.0.1:19000\nx-amz-content-sha256:' + digest
                             + '\nx-amz-date:' + stamp + '\n')
        signed_headers = 'host;x-amz-content-sha256;x-amz-date'
        canonical = '\n'.join(['DELETE', path, '', canonical_headers, signed_headers, digest])
        scope = date + '/us-east-1/s3/aws4_request'
        to_sign = '\n'.join(['AWS4-HMAC-SHA256', stamp, scope,
                             hashlib.sha256(canonical.encode()).hexdigest()])
        secret = os.environ.get('MALL_MINIO_SECRET_KEY', 'mall-minio-local-only')
        key = ('AWS4' + secret).encode()
        for part in (date, 'us-east-1', 's3', 'aws4_request'):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        access = os.environ.get('MALL_MINIO_ACCESS_KEY', 'mall-local')
        authorization = ('AWS4-HMAC-SHA256 Credential=' + access + '/' + scope
                         + ', SignedHeaders=' + signed_headers + ', Signature=' + signature)
        request = urllib.request.Request('http://127.0.0.1:19000' + path, data=b'', method='DELETE',
            headers={'Authorization': authorization, 'x-amz-date': stamp,
                     'x-amz-content-sha256': digest})
        with opener.open(request, timeout=30) as response:
            assert response.status == 204, 'Storage cleanup did not return 204'
        try:
            raw('http://127.0.0.1:19000' + path)
        except urllib.error.HTTPError as error:
            assert error.code == 404, 'Object is not confirmed deleted'
        else:
            raise AssertionError('Test object is still readable')
        return {'deleted_and_not_found': True}

    admin_token = member_token = object_name = history_id = None
    try:
        check('admin Swagger/OpenAPI', lambda: document(admin, 'default'))
        check('portal Swagger/OpenAPI', lambda: document(portal, 'mall-portal'))
        admin_token = login(admin, '/admin/login', {'username': 'admin', 'password': 'admin123'})
        check('admin demo login', lambda: {'authenticated': bool(admin_token)})
        products = api(admin, '/product/list?pageNum=1&pageSize=5', admin_token)
        assert products.get('list'), 'No seeded products'
        check('protected product list', lambda: {'total': products['total']})
        product_id = products['list'][0]['id']
        member_token = login(portal, '/sso/login', {'username': 'demo', 'password': 'demo123'}, True)
        check('member demo login', lambda: {'authenticated': bool(member_token)})

        def home():
            data = api(portal, '/home/content')
            assert any(data.get(key) for key in ('brandList', 'newProductList', 'hotProductList'))
            return {'sections': list(data)}
        check('seeded storefront home', home)

        # Record IDs before writing so only this run's record is removed.
        before = api(portal, '/member/readHistory/list?pageSize=100', member_token)
        before_ids = {item['id'] for item in before['list']}

        def mongo():
            nonlocal history_id
            api(portal, '/member/readHistory/create', member_token, body={'productId': product_id})
            after = api(portal, '/member/readHistory/list?pageSize=100', member_token)
            created = [item for item in after['list'] if item['id'] not in before_ids
                       and item['productId'] == product_id]
            assert len(created) == 1, 'New MongoDB record is not unique'
            history_id = created[0]['id']
            return {'created_and_read': True}
        check('MongoDB history write/read', mongo)

        def upload():
            nonlocal object_name
            boundary = 'mall-' + uuid.uuid4().hex
            filename = 'docker-smoke-' + uuid.uuid4().hex + '.txt'
            content = b'jd-mall Docker acceptance\n'
            payload = ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="'
                       + filename + '"\r\nContent-Type: text/plain\r\n\r\n').encode()
            payload += content + ('\r\n--' + boundary + '--\r\n').encode()
            result = json.loads(raw(admin + '/minio/upload', payload,
                {'Authorization': admin_token, 'Content-Type': 'multipart/form-data; boundary=' + boundary}))
            assert result.get('code') == 200, 'MinIO upload failed'
            url = urllib.parse.urlsplit(result['data']['url'])
            assert url.hostname in ('localhost', '127.0.0.1') and url.port == 19000
            assert url.path.startswith('/mall/')
            object_name = urllib.parse.unquote(url.path[len('/mall/'):])
            assert raw(result['data']['url']) == content, 'Uploaded object content differs'
            return {'uploaded_and_publicly_read': True}
        check('MinIO upload/public read', upload)
    except Exception as error:
        report['passed'] = False
        report['error'] = type(error).__name__
    finally:
        if history_id:
            try:
                check('MongoDB test record cleanup', lambda: api(portal, '/member/readHistory/delete',
                      member_token, form={'ids': history_id}))
            except Exception:
                report['passed'] = False
        if object_name:
            try:
                check('MinIO test object cleanup', lambda: remove_test_object(object_name))
            except Exception:
                report['passed'] = False
        report['passed'] = report.get('passed', True) and all(c['passed'] for c in report['checks'])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
