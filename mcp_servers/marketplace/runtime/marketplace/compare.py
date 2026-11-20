"""Compare reviewed, equivalent variants by purchase cost and delivery date."""
from decimal import Decimal,InvalidOperation,ROUND_CEILING
from statistics import median
import datetime as dt
from common import now


def positive(value):
    try:v=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):raise ValueError('invalid_quantity_or_price')
    if not v.is_finite() or v<=0:raise ValueError('invalid_quantity_or_price')
    return v


def compare(products,quantity=1,unit='pcs',budget=None,deadline=None,policy=None):
    policy=policy or {};demand=positive(quantity);rows=[]
    max_age=positive(policy.get('max_age_hours',12));clock=dt.datetime.now(dt.timezone.utc)
    for p in products:
        review=p.get('review') or {};reasons=[];warnings=list(p.get('warnings') or [])
        if p.get('stage')!='product' or not p.get('variant_verified'):reasons.append('exact_variant_not_verified')
        if review.get('compatible') is not True or not review.get('complete') or review.get('unresolved'):reasons.append('technical_review_required')
        if review.get('unit')!=unit:reasons.append('unit_mismatch')
        pack=review.get('pack_size');packs=None
        if pack is None:reasons.append('pack_size_unknown')
        else:
            try:packs=int((demand/positive(pack)).to_integral_value(rounding=ROUND_CEILING))
            except ValueError:reasons.append('pack_size_invalid')
        if p.get('available') is False or (p.get('stock') is not None and packs is not None and p['stock']<packs):reasons.append('insufficient_stock')
        price=p.get('price') or {};value=price.get('unit');goods=None;per_unit=None
        if price.get('currency')!='RUB':reasons.append('currency_not_comparable')
        if value is None:reasons.append('price_unknown')
        else:
            try:
                value=positive(value)
                if packs is not None:goods=value*packs;per_unit=value/positive(pack)
            except ValueError:reasons.append('price_invalid')
        shipping=p.get('delivery') or {};cost=shipping.get('cost');full=None
        if cost is not None and shipping.get('currency')=='RUB' and shipping.get('quantity')==packs:
            try:
                fee=Decimal(str(cost))
                if fee.is_finite() and fee>=0 and goods is not None:full=goods+fee
            except InvalidOperation:pass
        if full is None:warnings.append('total_cost_unknown')
        latest=shipping.get('latest');confirmed=bool(latest and shipping.get('verified') and shipping.get('destination'))
        if not confirmed:warnings.append('delivery_date_unconfirmed')
        try:
            stamp=dt.datetime.fromisoformat(p['observed_at'])
            if stamp.tzinfo is None or (clock-stamp).total_seconds()>float(max_age)*3600 or stamp>clock+dt.timedelta(minutes=5):reasons.append('refresh_product_required')
        except (ValueError,TypeError,KeyError):reasons.append('observation_time_unknown')
        seller=p.get('seller') or {};rating=seller.get('rating') or {};value5=None
        if rating.get('value') is not None and rating.get('scale') in (5,100):value5=Decimal(str(rating['value']))*5/Decimal(str(rating['scale']))
        if value5 is None:warnings.append('seller_rating_unknown')
        if policy.get('min_seller_rating_5') is not None and (value5 is None or value5<Decimal(str(policy['min_seller_rating_5']))):reasons.append('seller_rating_requirement')
        if budget is not None and (goods is not None and goods>positive(budget) or full is not None and full>positive(budget)):reasons.append('budget_exceeded')
        if budget is not None and full is None:warnings.append('budget_not_fully_verified')
        if deadline and (not confirmed or latest>deadline):reasons.append('delivery_deadline_unverified_or_missed')
        rows.append({'ref':p['ref'],'provider':p['provider'],'url':p['url'],'title':p.get('title'),'quantity':packs,'pack_size':pack,
            'goods_cost':str(goods) if goods is not None else None,'total_cost':str(full) if full is not None else None,
            'unit_cost':str(per_unit) if per_unit is not None else None,'currency':'RUB','delivery':shipping,
            'seller':seller,'product_rating':p.get('product_rating'),'options':p.get('options'),'eligible':not reasons,
            'reasons':reasons,'warnings':warnings,'price_explained':review.get('price_explained',False)})
    # One vote per independent seller, after compatibility and pack conversion.
    votes={}
    for row in rows:
        if row['eligible'] and row['unit_cost'] is not None:
            seller=row['seller'];key=(row['provider'],seller.get('id') or seller.get('url') or seller.get('name') or row['ref'])
            value=Decimal(row['unit_cost']);votes[key]=min(value,votes.get(key,value))
    center=median(votes.values()) if len(votes)>=3 else None
    factor=positive(policy.get('high_price_factor',10))
    if factor<=1:raise ValueError('outlier_factor_must_exceed_one')
    if center:
        for row in rows:
            if row['unit_cost'] is not None and Decimal(row['unit_cost'])>=center*factor:
                row['warnings'].append('current_price_outlier')
                if not row['price_explained']:row['eligible']=False;row['reasons'].append('price_outlier_requires_explanation')
    eligible=[r for r in rows if r['eligible']]
    eligible.sort(key=lambda r:(Decimal(r['goods_cost']),r['ref']))
    complete=[r for r in eligible if r['total_cost'] is not None and r['delivery']['verified'] and r['delivery'].get('destination')]
    cheapest=min(complete,key=lambda r:Decimal(r['total_cost'])) if complete else None
    fastest=min(complete,key=lambda r:(r['delivery']['latest'],Decimal(r['total_cost']))) if complete else None
    premium=Decimal(str(policy.get('fast_delivery_premium',.20)))
    if not premium.is_finite() or premium<0:raise ValueError('invalid_delivery_premium')
    balanced=None
    if cheapest:
        near=[r for r in complete if Decimal(r['total_cost'])<=Decimal(cheapest['total_cost'])*(1+premium)]
        balanced=min(near,key=lambda r:(r['delivery']['latest'],Decimal(r['total_cost'])))
    certain=len(complete)==len(eligible) and bool(eligible)
    picks=[]
    if certain and balanced:picks=[balanced['ref']]
    elif eligible:
        choices=([balanced] if balanced else [])+([eligible[0]] if eligible else [])
        dated=sorted([r for r in eligible if r['delivery'].get('latest')],key=lambda r:(r['delivery']['latest'],Decimal(r['goods_cost'])))
        choices+=dated[:2]
        for row in choices:
            if row and row['ref'] not in picks:picks.append(row['ref'])
        for row in eligible:
            if len(picks)>=3:break
            if row['ref'] not in picks:picks.append(row['ref'])
    return {'status':'ok','observed_at':now(),'currency':'RUB','requested_quantity':str(demand),'unit':unit,
        'recommended':picks,'decision':'best_verified' if certain else 'offer_choices' if eligible else 'no_eligible_offers',
        'cheapest_verified':cheapest['ref'] if cheapest else None,'fastest_verified':fastest['ref'] if fastest else None,
        'balanced_verified':balanced['ref'] if balanced else None,'offers':rows,
        'comparison_complete':certain,'independent_sellers':len(votes),'median_unit_cost':str(center) if center is not None else None,
        'warnings':[] if certain else ['Price/delivery coverage is incomplete; recommendations are alternatives, not a proven overall winner.']}
