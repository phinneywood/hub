"""Send the morning “Needs you” nudge to the owner's phone through Home Assistant.

Run by a LaunchAgent on the Mac mini. Reads Hub's own /today/brief on loopback and
sends only the count plus a link that opens Hub; item titles stay in Hub. First asks
Hub to run its overnight review, which Hub limits to once a day. Sends
nothing on a clear day. If Hub does not answer, says so instead of staying silent.
Machine bindings come from the environment, never from this repository:
  HUB_BRIEF_SOURCE    loopback address of /today/brief
  HUB_BRIEF_LINK      private Hub address opened when the notification is tapped
  HUB_BRIEF_NOTIFY    Home Assistant notify service name, e.g. mobile_app_phone
  HUB_BRIEF_HA_HELPER directory holding the Home Assistant maintenance helper
"""
import os
import re
import sys
import urllib.request

CLEAR = 'Nothing needs you today.'
PREPARE_SECONDS = 300


def message(brief):
    """Notification text for a brief, or None when nothing should be sent."""
    first = (brief or '').strip().splitlines()[0] if (brief or '').strip() else ''
    if not first or first == CLEAR:
        return None
    return first.rstrip(':').replace(' for today', ' in Hub for today')


def read(source):
    if not re.fullmatch(r'http://127\.0\.0\.1:\d+/today/brief', source):
        raise ValueError('HUB_BRIEF_SOURCE must be the loopback /today/brief address')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:  # the review is optional: an older Hub, a limit or a model outage must not stop the nudge
        prepare = urllib.request.Request(source[:-len('brief')] + 'prepare', data=b'', method='POST',
                                         headers={'Sec-Fetch-Site': 'none'})
        opener.open(prepare, timeout=PREPARE_SECONDS).close()
    except Exception:  # noqa: BLE001
        pass
    with opener.open(source, timeout=30) as reply:
        return reply.read().decode()


def main(env=os.environ, send=None):
    service, link = env['HUB_BRIEF_NOTIFY'], env['HUB_BRIEF_LINK']
    if not re.fullmatch(r'[a-z0-9_]+', service) or not link.startswith('https://'):
        raise ValueError('Invalid notify service or link')
    if send is None:
        sys.path.insert(0, env['HUB_BRIEF_HA_HELPER'])
        import maintenance
        send = lambda body: maintenance.api('POST', '/api/services/notify/' + service, body)  # noqa: E731
    try:
        text = message(read(env['HUB_BRIEF_SOURCE']))
    except Exception:  # noqa: BLE001 - a silent morning must not look like a clear one
        text = 'Hub didn’t answer this morning.'
    if text is None:
        return 'clear'
    send({'title': 'Hub', 'message': text, 'data': {'url': link}})
    return 'sent'


if __name__ == '__main__':
    print(main())
