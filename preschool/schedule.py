"""Source-reviewed local class timetable. Never creates or changes Google events."""
import hashlib
import re
from datetime import date, datetime, timedelta

from .extract import safe_url

WEEKDAYS=('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')


def source_key(source):
    return hashlib.sha256(('public-class-source\0'+source['url']+'\0'+source['quote']).encode()).hexdigest()


def validate(payload):
    fields={'title','weekdays','start_date','end_date','start_time','end_time','location','details','source_keys','exceptions','public_sources'}
    if not isinstance(payload,dict) or set(payload)!=fields:
        raise ValueError('Invalid class timetable fields.')
    if not payload['title'].strip() or len(payload['title'])>200 or len(payload['details'])>1200 or len(payload['location'])>300:
        raise ValueError('Invalid class timetable text.')
    days=payload['weekdays']
    if not isinstance(days,list) or not days or len(days)>7 or any(type(day) is not int or not 0<=day<=6 for day in days):
        raise ValueError('Choose explicit weekdays.')
    start=date.fromisoformat(payload['start_date']);end=date.fromisoformat(payload['end_date'])
    if end<start or (end-start).days>400:
        raise ValueError('A class timetable needs a bounded, verified term.')
    def clock(value):
        if value is not None:
            if not isinstance(value,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value):
                raise ValueError('Use an explicit local class time.')
    clock(payload['start_time']);clock(payload['end_time'])
    if (payload['start_time'] is None)!=(payload['end_time'] is None):
        raise ValueError('Provide both class times or neither.')
    if payload['start_time'] and payload['end_time']<=payload['start_time']:
        raise ValueError('Class must end after it starts.')
    keys=payload['source_keys']
    if not keys or len(keys)>100 or any(not re.fullmatch(r'[a-f0-9]{64}',key) for key in keys):
        raise ValueError('The class timetable needs supporting sources.')
    if len(payload['public_sources'])>20:raise ValueError('Too many public references.')
    for source in payload['public_sources']:
        if set(source)!={'url','quote','title'} or not safe_url(source['url'],source['url']) or not source['quote'].strip() or len(source['quote'])>500 or len(source['title'])>200:
            raise ValueError('Invalid public timetable reference.')
        from .sources import normalize_url
        if not normalize_url(source['url'])[0]:raise ValueError('Timetable reference is not on the public allowlist.')
    if len(payload['exceptions'])>100:raise ValueError('Too many timetable exceptions.')
    seen=set()
    for exception in payload['exceptions']:
        if set(exception)!={'date','kind','title','details','location','start_time','end_time','source_keys'}:
            raise ValueError('Invalid timetable exception.')
        day=date.fromisoformat(exception['date'])
        if day<start or day>end or day.weekday() not in days or day in seen:
            raise ValueError('An exception must identify one scheduled class date.')
        seen.add(day)
        if exception['kind'] not in ('cancelled','changed'):raise ValueError('Invalid class change.')
        if not exception['title'].strip() or len(exception['title'])>200 or len(exception['details'])>1200 or len(exception['location'])>300:
            raise ValueError('Invalid class change text.')
        clock(exception['start_time']);clock(exception['end_time'])
        if (exception['start_time'] is None)!=(exception['end_time'] is None):raise ValueError('Provide both changed times or neither.')
        if exception['start_time'] and exception['end_time']<=exception['start_time']:raise ValueError('Invalid changed class times.')
        if not exception['source_keys'] or any(not re.fullmatch(r'[a-f0-9]{64}',key) for key in exception['source_keys']):
            raise ValueError('A class change needs source evidence.')
    return payload


def public_references(timetables):
    result={}
    for timetable in timetables:
        for source in timetable['payload']['public_sources']:
            key=source_key(source)
            result[key]={'view_key':key,'gmail_url':source['url'],'url':source['url'],
                         'title':source['title'],'source_quote':source['quote'],
                         'kind':'resource','details':'Public reference for the class timetable.',
                         'date':None,'time':None,'location':None,'uncertainty':'',
                         'dismissed':True,'reference_only':True,'saved_at':timetable['updated']}
    return list(result.values())


def display_time(start,end):
    if not start:return None
    def label(value):return datetime.strptime(value,'%H:%M').strftime('%-I:%M %p')
    return label(start)+' – '+label(end)+' Pacific'


def occurrences(timetables,today,sources,horizon=90):
    if not 1<=horizon<=90:raise ValueError('Class previews are bounded to 90 days.')
    today=date.fromisoformat(today)
    known={source['view_key']:source for source in sources}
    rows=[]
    for timetable in timetables:
        rule=validate(timetable['payload'])
        required=set(rule['source_keys'])
        # Missing evidence never silently creates scheduled classes.
        if not required<=known.keys():continue
        exceptions={row['date']:row for row in rule['exceptions']}
        cursor=max(today,date.fromisoformat(rule['start_date']))
        end=min(today+timedelta(days=horizon-1),date.fromisoformat(rule['end_date']))
        while cursor<=end:
            day=cursor.isoformat()
            if cursor.weekday() in rule['weekdays']:
                changed=exceptions.get(day)
                if changed and not set(changed['source_keys'])<=known.keys():
                    cursor+=timedelta(days=1);continue
                data=changed or rule
                key=hashlib.sha256((timetable['slug']+'\0'+day).encode()).hexdigest()[:24]
                source_keys=set(rule['source_keys']) | set(data['source_keys'])
                rows.append({'key':key,'topic':'dates','kind':'event','title':data['title'],
                    'details':data['details'],'date':day,'time':None if changed and changed['kind']=='cancelled' else display_time(data['start_time'],data['end_time']),
                    'location':data['location'],'url':None,'uncertainty':'','scheduled':True,
                    'schedule_status':changed['kind'] if changed else 'regular',
                    'sources':[known[key] for key in sorted(source_keys)],
                    'href':'/preschool/dates#fact-'+key})
            cursor+=timedelta(days=1)
    return sorted(rows,key=lambda row:(row['date'],row['title']))
