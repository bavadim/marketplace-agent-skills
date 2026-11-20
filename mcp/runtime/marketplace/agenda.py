#!/usr/bin/env python3
"""Build daily/weekly lists and pickup warnings from private marketplace snapshots."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
from common import now, read_json, write_private, TIMEZONE

TERMINAL={'delivered','cancelled','returned'}


def agenda(documents,start,days=7,warn_days=2,clock=None):
    clock=clock or dt.datetime.now(dt.timezone.utc)
    if isinstance(start,str):start=dt.date.fromisoformat(start)
    end=start+dt.timedelta(days=days-1);actual_today=clock.astimezone(ZoneInfo(TIMEZONE)).date()
    entries={};unresolved={};warnings=[];alerts=[];complete=True
    for document in documents:
        payload=document.get('data',document);site=document.get('marketplace') or payload.get('marketplace')
        if not payload.get('complete',False):complete=False
        if payload.get('status') not in (None,'ok','partial'):warnings.append({'marketplace':site,'reason':'source_failed'})
        observed=document.get('observed_at') or payload.get('observed_at')
        try:
            stamp=dt.datetime.fromisoformat(observed)
            if stamp.tzinfo is None:raise ValueError()
            stale=(clock-stamp).total_seconds()>86400 or stamp>clock+dt.timedelta(minutes=5)
        except (ValueError,TypeError):stale=True
        if stale:warnings.append({'marketplace':site,'reason':'snapshot_stale_or_undated'})
        for order in payload.get('orders',[]):
            market=order.get('marketplace') or site
            if not order.get('shipments'):
                unresolved[(market,order['id'])]={k:order.get(k) for k in ('marketplace','id','url','status','status_text','description','missing')}
                complete=False
            for shipment in order.get('shipments',[]):
                key=(market,order['id'],shipment['id'])
                row={k:shipment.get(k) for k in ('id','status','status_text','delivery_text','delivery_method','address','expected','pickup_until','pickup_until_inclusive','items')}
                row.update(marketplace=market,order_id=order['id'],source_url=order['url'],observed_at=observed,stale=stale)
                # Deduplicate overlapping snapshots, preferring their newest observation.
                if key not in entries or (observed or '')>(entries[key].get('observed_at') or ''):entries[key]=row
    ready=[];expected=[];undated=[];by_day={ (start+dt.timedelta(days=i)).isoformat():[] for i in range(days)}
    for key,row in entries.items():
        if row['status'] in TERMINAL:continue
        if row['status']=='ready_for_pickup':
            ready.append(row);deadline=row.get('pickup_until')
            remaining=(dt.date.fromisoformat(deadline)-actual_today).days if deadline else None
            severity='overdue' if remaining is not None and remaining<0 else 'deadline_soon' if remaining is not None and remaining<=warn_days else 'ready_for_pickup'
            alert={'kind':severity,'marketplace':row['marketplace'],'order_id':row['order_id'],'shipment_id':row['id'],
                   'address':row['address'],'pickup_until':deadline,'days_remaining':remaining,'stale':row['stale']}
            # Daily fingerprints allow tomorrow's urgency to differ without repeat notifications today.
            alert['fingerprint']=hashlib.sha256(json.dumps([key,severity,deadline,actual_today.isoformat() if severity in ('overdue','deadline_soon') else None],ensure_ascii=False).encode()).hexdigest()
            alerts.append(alert)
        else:
            window=row.get('expected') or {};lo=window.get('from');hi=window.get('to')
            if not lo or not hi:undated.append(row);continue
            if lo<=end.isoformat() and hi>=start.isoformat():
                expected.append(row)
                for day in by_day:
                    if lo<=day<=hi:by_day[day].append({'marketplace':row['marketplace'],'order_id':row['order_id'],
                        'shipment_id':row['id'],'address':row['address'],'certainty':'range' if lo!=hi else 'day'})
    groups={}
    for row in ready:groups.setdefault(row['address'] or 'Адрес неизвестен',[]).append({'marketplace':row['marketplace'],
        'order_id':row['order_id'],'shipment_id':row['id'],'pickup_until':row['pickup_until']})
    return {'schema_version':1,'generated_at':clock.isoformat(),'timezone':TIMEZONE,'period':{'from':start.isoformat(),'to':end.isoformat()},
            'complete':complete,'warnings':warnings,'ready_for_pickup':ready,'expected':expected,'undated':undated,
            'unresolved_orders':list(unresolved.values()),'by_day':by_day,'pickup_addresses':[{'address':a,'shipments':s} for a,s in groups.items()],
            'alerts':alerts,'receipt_codes_included':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    build=sub.add_parser('build');build.add_argument('--input',nargs='+',required=True,type=Path)
    build.add_argument('--date',default=dt.datetime.now(ZoneInfo(TIMEZONE)).date().isoformat())
    build.add_argument('--days',type=int,choices=range(1,32),default=7)
    build.add_argument('--warn-days',type=int,default=2);build.add_argument('--state',type=Path)
    ack=sub.add_parser('ack',help='Record only fingerprints successfully delivered by the caller')
    ack.add_argument('--state',required=True,type=Path);ack.add_argument('--fingerprint',action='append',required=True)
    for cmd in (build,ack):cmd.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    if args.command=='build':
        if not 0<=args.warn_days<=30:p.error('warn-days must be 0..30')
        result=agenda([read_json(f) for f in args.input],args.date,args.days,args.warn_days)
        seen=read_json(args.state).get('acknowledged',{}) if args.state and args.state.exists() else {}
        result['pending_alerts']=[a for a in result['alerts'] if a['fingerprint'] not in seen]
        # Never acknowledge before a channel actually reports delivery.
    else:
        import re
        if any(not re.fullmatch(r'[0-9a-f]{64}',f) for f in args.fingerprint):p.error('invalid fingerprint')
        seen=read_json(args.state).get('acknowledged',{}) if args.state.exists() else {}
        cutoff=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=60)).isoformat()
        seen={k:v for k,v in seen.items() if v>=cutoff}
        for fingerprint in args.fingerprint:seen[fingerprint]=now()
        write_private(args.state,{'acknowledged':seen});result={'status':'ok','acknowledged':len(args.fingerprint)}
    write_private(args.output,result);print(json.dumps({'status':'ok','output':str(args.output)},ensure_ascii=False))

if __name__=='__main__':main()
