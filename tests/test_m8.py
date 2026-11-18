import pytest
from decimal import Decimal
from app import store
from app.modules.m8_clock import rules
def make_run(client): return client.post("/api/runs",json={"sample":"drift.csv"}).get_json()
def correct(client,run_id,sample="drift_corrections.csv"): return client.post(f"/api/runs/{run_id}/corrections",json={"sample":sample})
def journeys():
    return [{"event_id":"A","card":"1","route":"01","at":"2026-11-16T00:00:00+00:00","business_day":"2026-11-16","base_fare":"2.50","reported":"2.50"},{"event_id":"B","card":"1","route":"02","at":"2026-11-16T00:30:00+00:00","business_day":"2026-11-16","base_fare":"2.50","reported":"2.50"}]
def baseline():
    return [{"card":"1","business_day":"2026-11-16","expected":"2.50"},{"card":"1","business_day":"2026-11-16","expected":"2.50"}]
def unit(corrections,max_drift="10"):
    return rules.adjudicate(journeys(),baseline(),corrections,{},Decimal(max_drift))
def test_real_run_baseline(client):
    p=client.post("/api/runs",json={"sample":"drift.csv"}).get_json()
    assert p["stages"][4]["metrics"]["expected_total"]=="18.00" and p["stages"][5]["metrics"]["balanced"] is True
def test_window_adjudicated_on_verified_clock(client):
    "TRAP-27 换乘窗口以核清后的调度校时为准"
    run=make_run(client);c=correct(client,run["run_id"]).get_json()
    assert c["status"]=="已核清"
    adjustments={a["card"]:a for a in c["adjustments"]}
    assert adjustments["0101"]["delta"]=="-1.00" and adjustments["0101"]["new_receivable"]=="4.00"
    assert adjustments["0102"]["delta"]=="1.00" and adjustments["0102"]["new_receivable"]=="5.00"
    assert adjustments["0103"]["delta"]=="-1.00"
    assert "0104" not in adjustments
def test_exact_window_edge_shifted_by_correction(client):
    run=make_run(client);c=correct(client,run["run_id"]).get_json()
    rows={r["event_id"]:r for r in c["rows"]}
    assert rows["D006"]["transfer_discount"]=="1.00" and rows["D004"]["transfer_discount"]=="0.00"
def test_unique_receivable_and_clearing(client):
    run=make_run(client);c=correct(client,run["run_id"]).get_json()
    clearing=c["clearing"]
    assert clearing["receivable_total"]=="13.00" and clearing["reported_total"]=="14.00"
    assert clearing["difference_total"]=="-1.00" and clearing["adjustment_total"]=="-1.00"
    ledger=store.ledger_for(run["run_id"])
    assert {(l["card"],l["business_day"]) for l in ledger}=={("0101","2026-11-16"),("0102","2026-11-16"),("0103","2026-11-16")}
    assert {l["card"]:l["receivable"] for l in ledger}=={"0101":"4.00","0102":"5.00","0103":"4.00"}
def test_rerun_never_double_books(client):
    "TRAP-28 重跑同一校时单不重复改账"
    run=make_run(client);a=correct(client,run["run_id"]).get_json();b=correct(client,run["run_id"]).get_json()
    assert a["correction_id"]==b["correction_id"] and len(store.corrections_for(run["run_id"]))==1
    assert store.ledger_for(run["run_id"])==store.ledger_for(run["run_id"]) and len(store.ledger_for(run["run_id"]))==3
def test_reissue_never_double_books(client):
    run=make_run(client);c=correct(client,run["run_id"]).get_json()
    before=store.ledger_for(run["run_id"])
    r=client.post(f"/api/corrections/{c['correction_id']}/reissue");assert r.status_code==200
    assert r.get_json()["reissued"]==3 and store.ledger_for(run["run_id"])==before
def test_failed_correction_keeps_old_version(client):
    "TRAP-30 校时失败保留旧版结果"
    run=make_run(client)
    c=client.post(f"/api/runs/{run['run_id']}/corrections",json={"sample":"drift_corrections_bad.csv"}).get_json()
    assert c["status"]=="校时失败" and {f["reason"] for f in c["failures"]}=={"DRIFT_EXCEEDED","UNKNOWN_EVENT"}
    assert store.ledger_for(run["run_id"])==[]
    assert store.get(run["run_id"])["stages"][4]["metrics"]["expected_total"]=="18.00"
def test_failed_correction_is_retained(client):
    run=make_run(client)
    client.post(f"/api/runs/{run['run_id']}/corrections",json={"sample":"drift_corrections_bad.csv"})
    kept=store.corrections_for(run["run_id"])
    assert len(kept)==1 and kept[0]["status"]=="校时失败" and kept[0]["failures"]
def test_seal_blocked_until_clock_verified(client):
    "TRAP-29 校时未核清不得封存日结"
    run=make_run(client)
    client.post(f"/api/runs/{run['run_id']}/corrections",json={"sample":"drift_corrections_bad.csv"})
    assert client.post(f"/api/runs/{run['run_id']}/seal").status_code==409
    correct(client,run["run_id"])
    sealed=client.post(f"/api/runs/{run['run_id']}/seal");assert sealed.status_code==200
    again=client.post(f"/api/runs/{run['run_id']}/seal").get_json()
    assert again["already"] is True and again["digest"]==sealed.get_json()["digest"]
def test_seal_without_corrections_allowed(client):
    run=make_run(client)
    assert client.post(f"/api/runs/{run['run_id']}/seal").status_code==200
def test_no_correction_after_seal(client):
    run=make_run(client);correct(client,run["run_id"]);client.post(f"/api/runs/{run['run_id']}/seal")
    assert correct(client,run["run_id"]).status_code==409
def test_reissue_failed_correction_rejected(client):
    run=make_run(client)
    c=client.post(f"/api/runs/{run['run_id']}/corrections",json={"sample":"drift_corrections_bad.csv"}).get_json()
    assert client.post(f"/api/corrections/{c['correction_id']}/reissue").status_code==409
def test_reissue_superseded_rejected(client):
    run=make_run(client);a=correct(client,run["run_id"]).get_json()
    csv="event_id,corrected_time,basis\nD002,2026-11-16T08:56:00+08:00,二次对时\n"
    b=client.post(f"/api/runs/{run['run_id']}/corrections",json={"csv":csv}).get_json()
    assert b["status"]=="已核清" and b["correction_id"]!=a["correction_id"]
    assert client.post(f"/api/corrections/{a['correction_id']}/reissue").status_code==409
    assert client.post(f"/api/corrections/{b['correction_id']}/reissue").status_code==200
def test_original_taps_retained(client):
    run=make_run(client);c=correct(client,run["run_id"]).get_json()
    events={e["event_id"]:e for e in c["events"]}
    assert events["D002"]["original_local"].endswith("09:01:00+08:00") and events["D002"]["corrected_local"].endswith("08:57:00+08:00") and events["D002"]["basis"]
    taps=store.get(run["run_id"])["stages"][0]["rows"]
    assert any(r["event_id"]=="D002" and r["event_time"]=="2026-11-16T09:01:00+08:00" for r in taps)
def test_correction_unknown_run_404(client): assert client.post("/api/runs/missing/corrections",json={"sample":"drift_corrections.csv"}).status_code==404
def test_correction_bad_header_422(client):
    run=make_run(client)
    assert client.post(f"/api/runs/{run['run_id']}/corrections",json={"csv":"a,b\n1,2\n"}).status_code==422
def test_clock_page(client):
    run=make_run(client);correct(client,run["run_id"])
    page=client.get("/clock");assert page.status_code==200
    text=page.get_data(as_text=True)
    assert "核清后的调度校时" in text and "调度台GPS对时" in text and "唯一应收" in text
def test_unit_order_violation():
    r=unit([{"event_id":"A","corrected_time":"2026-11-16T00:45:00+00:00","basis":"对时"}],"60")
    assert r["status"]=="校时失败" and r["failures"][0]["reason"]=="ORDER_VIOLATION"
def test_unit_day_shift():
    r=unit([{"event_id":"A","corrected_time":"2026-11-16T16:30:00+00:00","basis":"对时"}],"2000")
    assert r["failures"][0]["reason"]=="DAY_SHIFT"
def test_unit_duplicate_correction():
    rows=[{"event_id":"A","corrected_time":"2026-11-16T00:05:00+00:00","basis":"对时"}]*2
    assert unit(rows)["failures"][0]["reason"]=="DUPLICATE_CORRECTION"
def test_unit_empty_basis(): assert unit([{"event_id":"A","corrected_time":"2026-11-16T00:05:00+00:00","basis":""}])["failures"][0]["reason"]=="EMPTY_BASIS"
def test_unit_naive_time(): assert unit([{"event_id":"A","corrected_time":"2026-11-16T00:05:00","basis":"对时"}])["failures"][0]["reason"]=="INVALID_TIME"
def test_unit_drift_exceeded(): assert unit([{"event_id":"B","corrected_time":"2026-11-16T00:41:00+00:00","basis":"对时"}])["failures"][0]["reason"]=="DRIFT_EXCEEDED"
def test_unit_verified_transfer():
    r=unit([{"event_id":"B","corrected_time":"2026-11-16T00:29:00+00:00","basis":"对时"}])
    assert r["status"]=="已核清" and r["adjustments"][0]["delta"]=="-1.00" and r["adjustments"][0]["new_receivable"]=="4.00"
def test_parse_empty_and_columns():
    from app.errors import DomainError
    for raw,code in [(b"","EMPTY_FILE"),(b"event_id,corrected_time,basis\n","EMPTY_FILE"),(b"event_id,corrected_time,basis\nA,2026-11-16T00:05:00+00:00\n","BAD_COLUMNS")]:
        with pytest.raises(DomainError) as e: rules.parse(raw)
        assert e.value.code==code
