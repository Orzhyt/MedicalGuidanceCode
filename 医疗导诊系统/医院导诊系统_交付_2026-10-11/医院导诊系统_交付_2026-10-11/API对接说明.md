# 医院导诊接口对接说明 2.2

## 两套前台的显示范围

患者前台只显示咨询API的answer及预约API的message。完整参考资料、科室判断依据、JSON和内部编号不发到患者页面。

工作人员前台通过可信后端读取完整咨询记录。后端先验证工作人员身份及访问范围；服务密钥仅用于服务间鉴权，不能替代工作人员角色校验。

## 咨询

POST /v1/chat-messages，Authorization: Bearer <咨询应用密钥>。

```json
{"inputs":{},"query":"患者描述","conversation_id":"","response_mode":"streaming","user":"PATIENT-A"}
```

后续消息保留conversation_id和user。流式message_end后再读取本轮后台记录。阻塞模式使用blocking。患者只渲染answer。

## 后台记录

GET /api/consultations/<conversation_id> 返回turns和messages；GET /api/consultations/<conversation_id>/latest 返回latest。

都需X-Service-Key。每轮记录包括event_id、conversation_id、user_id、patient_query、patient_answer、result、state、model_output、knowledge、availability和recorded_at。完整记录不受模型近期上下文长度限制。

保存记录接口POST /api/consultations/record由咨询流程调用。记录不可用时不交接预约。前台后端并发发送同一对话消息时应串行处理，避免较早轮次覆盖较新选择。

## 预约交接

选定时段后，后台最新记录的result.booking_workflow_inputs已经是预约工作流的输入格式：

```json
{"booking_payload":"{\"action\":\"hold\",\"doctor_id\":\"2\",\"slot_id\":\"实际时段\",\"appointment_id\":\"\",\"request_id\":\"稳定编号\",\"patient_token\":\"\"}","patient_token":""}
```

booking_payload必须是JSON字符串，不能直接传JSON对象。后端验证当前登录身份与记录user_id一致，在患者点击预约后补齐patient_token，原样传入POST /v1/workflows/run：

```json
{"inputs":{"booking_payload":"<上述JSON字符串>","patient_token":"<签名患者令牌>"},"response_mode":"blocking","user":"PATIENT-A"}
```

Authorization为预约应用密钥。读取data.outputs.message、result_json、code、ok。result_json本身也是JSON字符串，仅后端解析，不整段显示给患者。

## 操作与状态

- query：doctor_id，查余号，不占号。
- hold：slot_id、request_id、令牌，原子占位180秒。
- confirm：appointment_id、新的request_id、令牌，正式预约。
- cancel：appointment_id、新的request_id、令牌，释放时段。
- status：appointment_id或原request_id、令牌，核查办理结果。
- list：令牌，查询本人预约。

HELD只表示临时保留，CONFIRMED才是成功。SLOT_TAKEN、HOLD_EXPIRED返回alternatives供患者重选，必须保留患者对号别、医生和时间的要求，不能自动替换。超时返回outcome_unknown，先按原request_id查询status，再决定是否用原编号重试；不要换编号盲目重复提交。

## 医院服务接口

服务地址本机http://127.0.0.1:8095；容器内http://medical-guidance:8095。

- GET /health：无需密钥。
- GET /api/doctors、/api/hospitals：医院与医生目录。
- GET /api/booking/options?specialty_code=digestive：科室医生及实际可预约余号。
- GET /api/doctors/<doctor_id>/slots：指定医生余号。
- POST /api/booking/run：预约事务接口，通常由预约流程调用。
- POST /api/patients/session：可信后端根据登录身份签发令牌，patient_id例如PATIENT-A，有效3600秒。
- GET /api/consultations/<conversation_id>及/latest：工作人员完整记录。

除health均需X-Service-Key。真实身份映射和工作人员授权由业务后端完成，患者不能自行指定他人身份。详细定义见openapi.json，调用示例见client_example.py。

## 对话可用性规则

科室方向明确就进入选号，不要求先确定具体疾病或填齐病情字段。病情追问每轮一个必要问题，最多三轮，已明确专科不因达到轮数而改全科。

新主诉尚未说明持续时间时，先做一句护士基础问询；耳鸣可同时问有无突然听力下降。已说明的信息不重复问，已经问过但记不清也不循环追问。明确紧急表现优先就医，不等待补字段。

预约日期已知就列余号，registration_type、period和doctor_id未提供时不限制查询，也不视为患者已同意任何选项。只在日期尚未明确时询问就诊日期。更改偏好会重新筛选；患者明确选定实际候选后才有booking_workflow_inputs。

## 两个工作流如何联动

1. 业务后端调用咨询 Chatflow，患者页面只显示 answer。同一会话保持 conversation_id、user，并串行发送。
2. 等待本轮完成，后端读取 latest，检查 user_id 与当前登录用户一致，并核对 event_id 对应本轮。未完成或旧轮次不能作为新的提交依据。
3. booking_ready 且 booking_workflow_inputs 存在表示可提交。只有一个候选时，“那就14号吧，帮我预约”等明确接受且条件相符会选中该号；仅查询或改条件不会自动选。
4. 当前接口约定在患者点击预约后，由后端获取 patient_token，复制私有输入，仅补充令牌，调用预约 Workflow。读取 HELD 后显示180秒保留倒计时，保存 appointment_id。
5. 患者确认后发送 confirm、新 request_id、appointment_id 和同一令牌；只有 CONFIRMED 展示预约成功。取消使用 cancel 和新编号。
6. SLOT_TAKEN 或 HOLD_EXPIRED 时让患者重选，原意向继续保留；结果不明先按原 request_id 核查 status，同操作重试沿用原编号。

当前 Dify 两个应用独立，没有自动桥接。预览咨询时“帮我预约”仅生成预约输入；若前台需要一句话直接触发提交，需另外实现可信提交事件和去重，不能仅凭 booking_ready 或 intent=choose 自动下单。咨询状态当前也不自动接收预约结果回写；前台单独展示预约 API 返回的 message 和状态。

client_example.py 提供后端调用封装。test_single_choice_public.py 已实际完成三轮单一候选对话及五次预约调用：占位、同请求重试、另一患者冲突、确认和取消。
