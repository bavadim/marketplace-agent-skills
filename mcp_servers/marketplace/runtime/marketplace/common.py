"""Shared private JSON, normalization and source-labelled measurements."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

SCHEMA_VERSION=1
TIMEZONE='Europe/Moscow'
MONTHS={'января':1,'февраля':2,'марта':3,'апреля':4,'мая':5,'июня':6,'июля':7,
        'августа':8,'сентября':9,'октября':10,'ноября':11,'декабря':12}


def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def today():return dt.datetime.now(ZoneInfo(TIMEZONE)).date()

def write_private(path,data):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    content=data if isinstance(data,bytes) else (json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode()
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        os.fchmod(stream.fileno(),0o600);stream.write(content)


def read_json(path):
    p=Path(path)
    if p.stat().st_size>32*1024*1024:raise ValueError('input_too_large')
    return json.loads(p.read_text())


def text(v):
    if isinstance(v,str):return re.sub(r'\s+',' ',v).strip()
    if isinstance(v,list):return ' '.join(filter(None,(text(x) for x in v)))
    if isinstance(v,dict):
        for key in ['text','content','title','name']:
            if key in v:return text(v[key])
    return ''


def status_code(label):
    label=text(label).lower()
    if re.search(r'отмен|возврат|вернул',label):return 'cancelled' if 'отмен' in label else 'returned'
    if re.search(r'можно забирать|готов.{0,20}выдач|ожидает получения|(?:прибыл|поступил|доставлен).{0,30}(?:пункт|постамат)',label):return 'ready_for_pickup'
    if re.search(r'не (?:получен|доставлен)',label):return 'unknown'
    if re.search(r'\bполучен[аы]?\b|\bдоставлен[аы]?\b|оставили у двери',label):return 'delivered'
    if re.search(r'курьер.{0,20}(едет|достав)|передан курьеру',label):return 'out_for_delivery'
    if re.search(r'в пути|службе доставки|отправлен|транзит',label):return 'in_transit'
    if re.search(r'сбор|готовим|оплачен|обрабатыва',label):return 'processing'
    if re.search(r'оплат',label):return 'awaiting_payment'
    return 'unknown'


def date_window(label,reference=None,context='future'):
    """Parse Russian displayed dates/ranges without treating business hours as ETA."""
    ref=dt.date.fromisoformat(reference) if isinstance(reference,str) else reference or today()
    source=text(label);s=source.lower();dates=[]
    for word,offset in [('послезавтра',2),('завтра',1),('сегодня',0),('вчера',-1)]:
        if re.search(r'\b'+word+r'\b',s):
            dates=[ref+dt.timedelta(days=offset)];break
    if not dates:
        cross=re.search(r'(\d{1,2})\s+('+'|'.join(MONTHS)+r')\s*[-‑–—]\s*(\d{1,2})\s+('+'|'.join(MONTHS)+r')(?:\s+(20\d{2}))?',s)
        if cross:
            try:
                year=int(cross[5] or ref.year)
                start=dt.date(year,MONTHS[cross[2]],int(cross[1]));end=dt.date(year,MONTHS[cross[4]],int(cross[3]))
                if end<start:end=end.replace(year=year+1)
                if not cross[5] and context=='future' and end<ref-dt.timedelta(days=90):
                    start=start.replace(year=start.year+1);end=end.replace(year=end.year+1)
                dates=[start,end]
            except ValueError:pass
    if not dates:
        match=re.search(r'(?:(\d{1,2})\s*[-‑–—]\s*)?(\d{1,2})\s+('+'|'.join(MONTHS)+r')(?:\s+(20\d{2}))?',s)
        if match:
            month=MONTHS[match[3]];year=int(match[4] or ref.year)
            try:
                end=dt.date(year,month,int(match[2]));start=dt.date(year,month,int(match[1] or match[2]))
                if not match[4]:
                    if context=='past' and start>ref+dt.timedelta(days=7):start=start.replace(year=year-1);end=end.replace(year=year-1)
                    elif context=='future' and end<ref-dt.timedelta(days=90):start=start.replace(year=year+1);end=end.replace(year=year+1)
                dates=[start,end]
            except ValueError:pass
    if not dates:
        match=re.search(r'\b(20\d{2}-\d{2}-\d{2})\b',s)
        if match:
            try:dates=[dt.date.fromisoformat(match[1])]
            except ValueError:pass
    times=re.findall(r'\b((?:[01]?\d|2[0-3]):[0-5]\d)\b',s)
    return {'from':dates[0].isoformat() if dates else None,'to':dates[-1].isoformat() if dates else None,
            'time_from':times[0] if len(times)>=2 else None,'time_to':times[1] if len(times)>=2 else None,
            'timezone':TIMEZONE,'source_text':source,'precision':'range' if len(dates)==2 and dates[0]!=dates[-1] else 'day' if dates else 'unknown'}


def measurements(attributes,source_url=None):
    weights=[];dimensions=[];review=[]
    for row in attributes:
        name=text(row.get('name'));value=text(row.get('value'));low=name.lower();line=name+': '+value
        evidence={'source_url':source_url,'source_text':line,'evidence_id':hashlib.sha256(line.encode()).hexdigest()[:16]}
        package=bool(re.search(r'упаков|брутто|посыл',low))
        if re.search(r'вес|масса',low):
            m=re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*(кг|kg|г|гр|g)?\s*',value.lower())
            unit=m[2] if m else None
            if not unit:
                u=re.search(r'(?:,|\(|\s)(кг|kg|г|гр|g)\)?$',low);unit=u[1] if u else None
            if m and unit:
                kg=float(m[1].replace(',','.'))/(1000 if unit in ('г','гр','g') else 1)
                weights.append({'kg':kg,'kind':'package' if package else 'product','confidence':'parsed',**evidence})
            else:review.append({'field':'weight',**evidence})
        if re.search(r'габарит|размеры|размер.*упаков|(?:длина|ширина|высота)',low):
            unit=re.search(r'(мм|см|метр|cm|mm|\bm\b)',low+' '+value.lower())
            scale={'мм':0.1,'mm':0.1,'см':1,'cm':1,'метр':100,'m':100}.get(unit[1]) if unit else None
            triple=re.search(r'(\d+(?:[.,]\d+)?)\s*[xх×*]\s*(\d+(?:[.,]\d+)?)\s*[xх×*]\s*(\d+(?:[.,]\d+)?)',value)
            axis=next((v for k,v in [('длина','length'),('ширина','width'),('высота','height')] if k in low),None)
            single=re.match(r'^\s*(\d+(?:[.,]\d+)?)\s*(?:мм|см|mm|cm|м)?\s*$',value)
            if triple and scale:
                dimensions.append({'cm':[float(triple[i].replace(',','.'))*scale for i in (1,2,3)],'axis_order':'as_listed','kind':'package' if package else 'product','confidence':'parsed',**evidence})
            elif axis and single and scale:
                dimensions.append({'axis':axis,'cm':float(single[1].replace(',','.'))*scale,'kind':'package' if package else 'product','confidence':'parsed',**evidence})
            else:review.append({'field':'dimensions',**evidence})
    coverage={}
    for kind in ('product','package'):
        own=[d for d in dimensions if d['kind']==kind]
        coverage[kind]={'weight':any(w['kind']==kind for w in weights),
                        'dimensions':any(isinstance(d['cm'],list) for d in own) or {d.get('axis') for d in own}>={'length','width','height'}}
    return {'weights':weights,'dimensions':dimensions,'needs_review':review,'completeness':coverage,
            'unknown':not any(c['weight'] and c['dimensions'] for c in coverage.values())}
