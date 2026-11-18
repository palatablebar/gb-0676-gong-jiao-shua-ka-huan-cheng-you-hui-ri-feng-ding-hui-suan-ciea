from flask import Blueprint,request,jsonify
from app import store
from app.errors import DomainError
from app.values import number
from app.modules.m8_clock import rules
bp=Blueprint("clock",__name__)
@bp.post("/api/runs/<run_id>/corrections")
def create(run_id):
    run=store.get(run_id)
    if store.seal_for(run_id): raise DomainError("SEALED","日结已封存，不得再提交校时",409)
    body=request.get_json(silent=True)
    if not isinstance(body,dict): raise DomainError("BAD_BODY","请求需要对象")
    raw=body.get("csv")
    if raw is not None and not isinstance(raw,str): raise DomainError("BAD_CSV","CSV需要文本")
    data=raw.encode() if raw is not None else rules.load_sample(body.get("sample","drift_corrections.csv"))
    params=body.get("parameters",{})
    if not isinstance(params,dict): raise DomainError("BAD_PARAMETERS","参数需要对象")
    max_drift=number(params.get("max_drift_minutes","10"))
    if max_drift<0: raise DomainError("BAD_DRIFT","漂移容忍不能为负")
    corrections=rules.parse(data)
    payload=rules.adjudicate(run["stages"][3]["rows"],run["stages"][4]["rows"],corrections,run.get("parameters",{}),max_drift)
    return jsonify(store.save_correction(rules.correction_id(run_id,data),run_id,payload))
@bp.get("/api/runs/<run_id>/corrections")
def listing(run_id):
    store.get(run_id)
    return {"corrections":store.corrections_for(run_id)}
@bp.get("/api/runs/<run_id>/clock")
def overview(run_id):
    store.get(run_id)
    return {"run_id":run_id,"corrections":store.corrections_for(run_id),"ledger":store.ledger_for(run_id),"seal":store.seal_for(run_id)}
@bp.post("/api/corrections/<correction_id>/reissue")
def reissue(correction_id): return jsonify(store.reissue(correction_id))
@bp.post("/api/runs/<run_id>/seal")
def seal(run_id): return jsonify(store.seal(run_id))
