"""在隔离演示 RabbitMQ 中验证容器重建后，持久队列及消息是否保留。"""
import argparse
import base64
import datetime
import json
import os
from pathlib import Path
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'verify'))
    parser.add_argument('--state', type=Path, required=True, help='保存本轮测试队列和消息标记')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'results/docker-persistence-local.json')
    args = parser.parse_args()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    password = os.environ.get('MALL_RABBIT_PASSWORD', 'mall-rabbit-local-only')
    auth = 'Basic ' + base64.b64encode(('mall:' + password).encode()).decode()

    def api(path, body=None, method='GET'):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request('http://127.0.0.1:15673/api/' + path, data=data,
            headers={'Authorization': auth, 'Content-Type': 'application/json'}, method=method)
        with opener.open(request, timeout=30) as response:
            payload = response.read()
            return json.loads(payload) if payload else None

    if args.mode == 'prepare':
        if args.state.exists():
            parser.error('状态文件已存在，请先核对并完成上一轮测试')
        state = {'queue': 'jd-mall-acceptance.persistence.' + uuid.uuid4().hex,
                 'payload': 'docker-persistence-' + uuid.uuid4().hex}
        api('queues/%2Fmall/' + state['queue'],
            {'durable': True, 'auto_delete': False, 'arguments': {}}, 'PUT')
        published = api('exchanges/%2Fmall/amq.default/publish',
            {'properties': {'delivery_mode': 2}, 'routing_key': state['queue'],
             'payload': state['payload'], 'payload_encoding': 'string'}, 'POST')
        assert published['routed'], 'Marker was not routed'
        args.state.write_text(json.dumps(state), encoding='utf-8')
        print('PASS durable queue and persistent marker published')
        return

    state = json.loads(args.state.read_text(encoding='utf-8'))
    assert state['queue'].startswith('jd-mall-acceptance.persistence.'), 'Not a test queue'
    path = 'queues/%2Fmall/' + state['queue']
    assert api(path)['durable'] is True, 'Queue is not durable'
    messages = api(path + '/get', {'count': 1, 'ackmode': 'ack_requeue_false',
                                 'encoding': 'auto', 'truncate': 1000}, 'POST')
    assert len(messages) == 1 and messages[0]['payload'] == state['payload'], 'Marker payload differs'
    assert messages[0]['properties']['delivery_mode'] == 2, 'Message was not persistent'
    api(path, method='DELETE')
    report = {'passed': True, 'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'scope': 'Compose down/up 保留卷，RabbitMQ 固定 hostname 后重建',
              'rabbit_hostname': 'rabbitmq', 'durable_queue_recovered': True,
              'persistent_message_payload_matched': True, 'marker_queue_deleted': True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    args.state.unlink()
    print('PASS persistent message recovered after recreation; test queue cleaned')


if __name__ == '__main__':
    main()
