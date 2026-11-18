# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

时钟校核：POST /api/runs/<id>/corrections接收sample或csv校时单（表头event_id,corrected_time,basis），可带parameters.max_drift_minutes（默认10分）。核清通过则以校时重算换乘窗并写入受影响卡当日唯一应收台账；核清失败只留痕不改账。同一校时单重复提交返回同一批次。POST /api/corrections/<id>/reissue补发入账，仅对最新已核清批次有效，重复补发不重复改账。POST /api/runs/<id>/seal封存日结，存在未核清校时时返回409，重复封存返回同一结果；封存后再提交校时返回409。GET /api/runs/<id>/clock返回校时批次、台账与封存状态。
