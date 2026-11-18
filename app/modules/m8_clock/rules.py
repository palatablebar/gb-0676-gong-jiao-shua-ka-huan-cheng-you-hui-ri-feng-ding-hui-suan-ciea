import csv,hashlib,io
from decimal import Decimal
from datetime import timezone,timedelta
from app.config import ROOT,RULE_VERSION
from app.errors import DomainError
from app.values import instant,number,money
from app.modules.m1_taps.rules import decode
from app.modules.m5_discounts.rules import evaluate as discount
COLUMNS=["event_id","corrected_time","basis"]
LOCAL=timezone(timedelta(hours=8))
SAMPLES={"drift_corrections.csv","drift_corrections_bad.csv"}
def load_sample(name):
    if name not in SAMPLES: raise DomainError("BAD_SAMPLE","校时样例不存在",404)
    return (ROOT/"sample_data"/name).read_bytes()
def local_day(at): return at.astimezone(LOCAL).date().isoformat()
def correction_id(run_id,raw): return hashlib.sha256(run_id.encode()+raw+RULE_VERSION.encode()).hexdigest()[:20]
def parse(raw):
    text,_=decode(raw)
    if not text or not text.strip(): raise DomainError("EMPTY_FILE","校时单为空")
    stream=csv.reader(io.StringIO(text,newline=""));header=next(stream,None)
    if header!=COLUMNS: raise DomainError("BAD_HEADER","校时单表头需为 event_id,corrected_time,basis")
    rows=[]
    for cells in stream:
        if not cells or not any(c.strip() for c in cells): continue
        if len(cells)!=len(COLUMNS): raise DomainError("BAD_COLUMNS","校时单列数不匹配")
        rows.append({"event_id":cells[0].strip(),"corrected_time":cells[1].strip(),"basis":cells[2].strip()})
    if not rows: raise DomainError("EMPTY_FILE","校时单为空")
    return rows
def adjudicate(journeys,baseline,corrections,parameters,max_drift):
    """裁决：换乘窗口以核清后的调度校时为准；任一核验失败则整单校时失败，保留旧版结果。"""
    by_id={j["event_id"]:j for j in journeys}
    failures=[];events=[];corrected={};seen=set()
    for c in corrections:
        event_id=c["event_id"]
        if event_id in seen: failures.append({"event_id":event_id,"reason":"DUPLICATE_CORRECTION","detail":"同一事件重复校时"});continue
        seen.add(event_id);journey=by_id.get(event_id)
        if journey is None: failures.append({"event_id":event_id,"reason":"UNKNOWN_EVENT","detail":"校时事件不在本批有效行程中"});continue
        if not c["basis"]: failures.append({"event_id":event_id,"reason":"EMPTY_BASIS","detail":"缺少校时依据"});continue
        try: target=instant(c["corrected_time"])
        except DomainError: failures.append({"event_id":event_id,"reason":"INVALID_TIME","detail":"校时时刻需要明确偏移"});continue
        original=instant(journey["at"]);drift=Decimal(str((target-original).total_seconds()))/Decimal(60)
        if abs(drift)>max_drift: failures.append({"event_id":event_id,"reason":"DRIFT_EXCEEDED","detail":f"漂移{drift}分超出容忍{max_drift}分"});continue
        if local_day(target)!=journey["business_day"]: failures.append({"event_id":event_id,"reason":"DAY_SHIFT","detail":"校时不得跨业务日"});continue
        corrected[event_id]=target
        events.append({"event_id":event_id,"card":journey["card"],"route":journey["route"],"original_at":journey["at"],"corrected_at":target.isoformat(),"original_local":original.astimezone(LOCAL).isoformat(),"corrected_local":target.astimezone(LOCAL).isoformat(),"drift_minutes":str(drift.quantize(Decimal("0.01"))),"basis":c["basis"]})
    if not failures:
        by_card={}
        for j in journeys: by_card.setdefault(j["card"],[]).append(j)
        for card,group in sorted(by_card.items()):
            ordered=sorted(group,key=lambda j:(instant(j["at"]),j["event_id"]))
            times=[corrected.get(j["event_id"],instant(j["at"])) for j in ordered]
            for index,(earlier,later) in enumerate(zip(times,times[1:])):
                if later<=earlier:
                    failures.append({"event_id":ordered[index+1]["event_id"],"reason":"ORDER_VIOLATION","detail":f"卡{card}校时后时刻次序倒置"});break
    if failures:
        return {"status":"校时失败","failures":sorted(failures,key=lambda f:(f["reason"],f["event_id"])),"events":events,"adjustments":[],"clearing":None,"rows":[],"metrics":{"corrected_events":len(events),"affected_cards":0}}
    recomputed=[{**j,"at":corrected[j["event_id"]].isoformat(),"business_day":local_day(corrected[j["event_id"]])} if j["event_id"] in corrected else j for j in journeys]
    rows=discount(recomputed,{"parameters":parameters})["rows"]
    old={};new={};paid={}
    for r in baseline: old[(r["card"],r["business_day"])]=old.get((r["card"],r["business_day"]),Decimal(0))+number(r["expected"])
    for r in rows:
        key=(r["card"],r["business_day"])
        new[key]=new.get(key,Decimal(0))+number(r["expected"]);paid[key]=paid.get(key,Decimal(0))+number(r["reported"])
    affected=[key for key in sorted(set(old)|set(new)) if new.get(key,Decimal(0))-old.get(key,Decimal(0))!=0]
    adjustments=[{"card":card,"business_day":day,"old_receivable":money(old.get((card,day),Decimal(0))),"new_receivable":money(new.get((card,day),Decimal(0))),"delta":money(new.get((card,day),Decimal(0))-old.get((card,day),Decimal(0)))} for card,day in affected]
    cards=[{"card":card,"business_day":day,"receivable":money(new[(card,day)]),"reported":money(paid.get((card,day),Decimal(0))),"difference":money(new[(card,day)]-paid.get((card,day),Decimal(0))),"delta":money(new[(card,day)]-old.get((card,day),Decimal(0)))} for card,day in affected]
    clearing={"cards":cards,"receivable_total":money(sum((new[key] for key in affected),Decimal(0))),"reported_total":money(sum((paid.get(key,Decimal(0)) for key in affected),Decimal(0))),"difference_total":money(sum((new[key]-paid.get(key,Decimal(0)) for key in affected),Decimal(0))),"adjustment_total":money(sum((new[key]-old.get(key,Decimal(0)) for key in affected),Decimal(0)))}
    metrics={"corrected_events":len(events),"affected_cards":len(affected),"original_expected_total":money(sum(old.values(),Decimal(0))),"corrected_expected_total":money(sum(new.values(),Decimal(0)))}
    return {"status":"已核清","failures":[],"events":events,"adjustments":adjustments,"clearing":clearing,"rows":rows,"metrics":metrics}
