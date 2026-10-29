---
name: wechat-group-daily
description: 回捞指定微信群某一天的聊天记录，由豆包逐条读消息、逐张看图，整理成“一条联系方式一条线索”的群日报，并生成一页式 HTML 和 Excel。当用户说“跑一下昨天的群日报”“看看几个群谁领了线索”“打开群日报”时使用。只读采集，不发送任何消息。
metadata:
  version: "0.2.0"
  display_name: "微信群日报"
  author: "rent-lead-hub"
---

# 微信群日报

## 这个技能做什么

把配置里的微信群，按指定日期回捞一遍，然后：

1. **豆包**逐片读聊天、逐张看图片，判断什么是线索、谁在领取、图片算不算线索。
2. **Python**只做机械辅助：取消息、排序、切片、列出图片路径、校验格式、算总数、渲染页面和 Excel。
3. 输出 `report.html`、`群日报_日期.xlsx`、`summary.json`、`facts.json`。

只统计群里看得见的内容。不发送消息、不加好友、不改群。

**必须装在登录微信的那台 Windows 电脑上。** 豆包要用工作模式在本机运行，云端沙盒看不到微信窗口。采集电脑要在目标日期之前就登录微信，并保持在线。

## 分工：豆包是分析员，Python 是工具

| 环节 | 谁做 |
| --- | --- |
| 切群、滚历史、合并窗口、补时间 | Python |
| 把消息切成 30–50 条一片，列出图片绝对路径 | Python |
| 判断什么是一条线索 | **豆包** |
| 判断“1 / 我要 / 接了 / 私聊 / 其他语义”是不是领取 | **豆包** |
| 判断图片是不是线索、图里有没有联系方式 | **豆包** |
| 校验 JSONL 格式和消息引用 | Python |
| 算总数、生成 HTML/Excel | Python |
| 给用户讲结论、回答追问 | 豆包 |

**不要用程序规则替自己判断线索和扣 1。** `scripts/report.py` 里的旧规则入口不是主流程；主流程是先由豆包产出 `leads.jsonl / claims.jsonl / images.jsonl`，再由 `finalize_agent.py` 合并渲染。

### 四条硬口径

1. **一个联系方式 = 一条线索**。一条消息或一张图里有多个联系方式，要拆成多条。
2. **任何人发的都算线索**。`config/groups.json` 里的发布人只是提示，不是限制。
3. **领取范围由豆包按语义判断**：`1`、`我要`、`接了`、`私聊`，以及其他表示领取的话都算；不按固定关键词硬切。
4. **图片由豆包逐张看**：它认为是线索才算；图里读出几个联系方式，就可能拆成几条线索。

## 第一次使用

1. 把 `wechat-group-daily.zip` 解压到本机技能目录，或让豆包从 GitHub 安装。
2. 双击 `setup.bat`，建 `.venv`、装 `wxauto4==41.1.7` 和 `openpyxl`。
3. 编辑 `config\groups.json`，填 7 个群的完整群名；发布人填了只作提示，不填也不影响“任何人可发线索”。
4. 登录微信，主窗口不要最小化。
5. 再按下面的标准流程跑。

所有命令都在技能根目录执行。如果存在 `.venv\Scripts\python.exe`，优先用它；下文用 `python` 代指。

## 标准流程

### 第 1 步：查状态

```bash
python scripts/status.py --date yesterday
```

看 `state`：

- `not_started`：还没采集，进第 2 步。
- `running`：正在回捞，告诉用户进度，不要重复启动。
- `ready_for_review`：工作台已生成，进第 3 步，开始整理。
- `reviewing`：豆包整理到一半，按 `next_group` / `next_slice` 接着做。
- `ready`：报告已生成，进第 5 步。
- `failed`：按“失败处理”做。

### 第 2 步：采集并生成工作台

```bash
python scripts/start_daily.py --date yesterday
```

它会后台回捞，约 15–25 分钟，立刻返回。告诉用户：

> 已经开始回捞了，大约 15–25 分钟。期间不要操作微信，让电脑保持亮屏。

**不要同步等待。** 用户过一会儿问“好了吗”，再查 `status.py`。等状态变成 `ready_for_review` 后，进入第 3 步。

### 第 3 步：豆包整理工作台（核心）

#### 3.1 先读工作台

`status.py` 会给出 `workdir`，例如：

```text
~/.rent-lead/wechat-group-daily/reports/2026-10-28/work
```

先读：

- `workdir/README.md`：工作台怎么用；
- `workdir/index.json`：有几个群、多少条消息、多少张图、多少片；
- 每个群的 `digest.md`：群名、消息量、图片量、覆盖范围、整理口径；
- 每个群的 `index.json`：切片清单；
- 每个群的 `image_index.json`：所有图片的 uid、绝对路径、前后文。

**一个群一个群做，不要一次把 7 个群全塞进上下文。** 工作台文件就是你的外部记忆。

#### 3.2 按切片顺序读

每个群有 `slices/part-001.md`、`part-002.md`……每片 30–50 条，带 5 条重叠。格式：

```text
[g1:0012] 19:48 | 发布人A | 文本 | wechat_001
[g1:0013] 19:48 | 发布人A | 图片 | 图片
    图片：C:\...\work\images\g1_img_0007.png
[g1:0014] 19:56 | 客服A | 文本 | 1
```

每条消息都有稳定的 `message_uid`（如 `g1:0012`）。**所有证据只写 uid，不要自己复制大段原文**；页面上的原文由程序回填。

#### 3.3 判断线索

对每一条消息/图片问自己：

- 这里面有没有**一个联系方式**（手机号、微信号、二维码、名片里的号）？
- 有，就建一条线索；
- 一条消息里有 3 个联系方式，就建 3 条线索，共用同一个 `source_message_uids`；
- 图片里有 2 个联系方式，就建 2 条线索，`source_type` 写 `image`，共用同一个图片 uid；
- 没有联系方式的房源广告、闲聊、表情包，不是线索；
- 客户、同行、发布人、任何人发的，只要有联系方式，都算。

`requirement` 里的区域、预算、户型、入住时间，能确定就填，不能确定就留空；**不要猜**。

#### 3.4 判断领取

看到一个像领取的消息，按上下文判断：

- 纯“1”→ 通常是领取最近的线索；
- “我要”“接了”“私聊”“算我一个”→ 也是领取；
- 引用某条消息回“1”→ 优先归被引用的线索；
- 一条“1”如果同时领走同批多条线索，可以写多条 `claims.jsonl`，共用同一个 `message_uid`，分别指向不同线索；
- 前面没有线索、或指代不清 → `target_lead_id` 留空，`status` 写 `orphan` 或 `uncertain`；
- 拿不准就写 `confidence` 低，并在 `open-questions.md` 记一笔，不要硬猜。

**第一个领取的人按时间排序由程序算；你只负责判断“这条消息是不是领取、指向哪条线索”。**

#### 3.5 图片必须逐张看

`image_index.json` 里的每张图都要打开看，不能跳过。用本地文件工具打开 `image_abs`，直接看，不需要 OCR。

判断并写入 `images.jsonl`：

- `lead`：图里有联系方式，是线索；图里有几个联系方式就建几条线索，`lead_ids` 全部列出；
- `attachment`：图是某条线索的补充（房源图、聊天截图补充），`lead_ids` 指向对应线索；
- `noise`：表情包、重复图、无关图；
- `uncertain`：看不清、图没落盘、拿不准，交人工核对。

如果图片未落盘（`saved=false`），写 `classification: "uncertain"`，在 `reason` 里写“图片未落盘”，不要假装看过。

#### 3.6 写文件格式

每个群目录下，边读边追加，**一行一个 JSON 对象，不要拆成多行**。

`leads.jsonl`：

```json
{"lead_id":"g1-L0001","group_key":"g1","contact":{"type":"phone","value":"13900000001","raw":"13900000001"},"requirement":{"area":"某区","budget_min":null,"budget_max":2500,"layout":"","move_in":"","notes":""},"publisher":"发布人A","time":"2026-10-28T19:48:00+08:00","source_message_uids":["g1:0012"],"evidence_uids":["g1:0012"],"source_type":"text","status":"unclaimed","confidence":0.95,"reason":"发布人发了手机号"}
```

`claims.jsonl`：

```json
{"claim_id":"g1-C0001","group_key":"g1","message_uid":"g1:0014","sender":"客服A","time":"2026-10-28T19:56:00+08:00","is_claim":true,"target_lead_id":"g1-L0001","confidence":0.95,"reason":"回复 1，紧跟该线索","status":"matched"}
```

`images.jsonl`：

```json
{"message_uid":"g1:0013","classification":"lead","lead_ids":["g1-L0002"],"extracted_contacts":[{"type":"phone","value":"150..."}],"extracted_requirement":{"area":"某区"},"confidence":0.9,"reason":"聊天截图里有客户电话"}
```

注意：

- `lead_id` 用 `群key-L序号`，`claim_id` 用 `群key-C序号`，不要重复；
- `contact` 只能有一个联系方式，不要写成 `contacts` 数组；
- `source_type`：`text` / `image` / `mixed`；
- `status`：线索用 `claimed` / `unclaimed` / `uncertain`；领取用 `matched` / `orphan` / `uncertain`；图片用 `lead` / `attachment` / `noise` / `uncertain`；
- `confidence` 0–1；低于 0.6 会被标“需核对”；
- 拿不准的写在 `open-questions.md`，不要编。

#### 3.7 每片收尾

每读完一片：

1. 在 `notes/part-XXX.md` 写这一片的观察、判断和疑问；
2. 运行：

```bash
python scripts/mark_slice.py --work "<workdir>" --group g1 --slice part-001
```

它会把 `progress.json` 往前推；断了以后看 `progress.json` 就能接着做。

### 第 4 步：校验

每个群做完，先跑：

```bash
python scripts/check_group.py --work "<workdir>" --group g1
```

全部群做完，再跑一次不带 `--group` 的：

```bash
python scripts/check_group.py --work "<workdir>"
```

看返回：

- `errors`：必须修，通常是 JSONL 格式、线索编号重复、领取指向不存在的线索、图片没判断、联系方式为空；
- `warnings`：可以保留，比如“这个群还没有领取记录”；
- 校验只查格式和引用，不判断语义；语义由你负责。

### 第 5 步：生成报告

```bash
python scripts/finalize_agent.py --work "<workdir>" --out "<date_dir>/report" --home "<home>"
```

`date_dir` 就是 `workdir` 的上一级，例如：

```text
...\reports\2026-10-28\work
...\reports\2026-10-28\report
```

它会：

- 把 `leads.jsonl / claims.jsonl / images.jsonl` 合并成 `facts.json`；
- 按 `message_uid` 回填原文和图片；
- 算总数、异常、摘要；
- 生成 `report.html` 和 `群日报_日期.xlsx`；
- 把状态改成 `ready`。

如果报 `workspace_incomplete`，回去把没标记的切片读完；不要用 `--allow-incomplete` 交付给客户。

### 第 6 步：给用户看和回答

```bash
python scripts/open_report.py --date yesterday
```

先读 `summary.json` 的 `brief_lines`，给用户 4–5 行结论，再打开报告。

用户追问细节时：

- 读 `summary.files.facts`；
- 线索在 `leads`，无主领取在 `orphans`，异常在 `attention`；
- 每条线索的 `evidence` 是原文证据，`claimers` 是领取人和顺序；
- 图片看 `evidence[].image_abs`，直接打开；
- 先回答结论，再补时间、发布人、联系方式、证据。

## 状态说明

- `not_started`：这一天没采集过。
- `running`：后台回捞中。
- `ready_for_review`：工作台已生成，等豆包整理。
- `reviewing`：豆包正在逐片整理；`done_slices / total_slices` 是进度。
- `ready`：报告已生成。
- `failed`：带 `error_code` 和 `log_tail`。

## 失败处理

| error_code | 对用户怎么说 | 怎么修 |
| --- | --- | --- |
| `dependency_missing` | 这台电脑还没装采集依赖 | 运行 `setup.bat` |
| `wechat_not_running` | 微信没开或没登录 | 登录微信，主窗口不要最小化 |
| `wechat_not_ready` | 微信挂不上 | 确认 4.1.8.x、已登录、窗口没最小化 |
| `config_invalid` | 群配置没填好 | 补 `config/groups.json` 的完整群名 |
| `probe_failed` | 采集中断了 | 看 `run.log` 最后 15 行 |
| `prepare_failed` | 工作台没生成 | 看 `run.log`，常见是图片路径或磁盘权限 |
| `agent_output_invalid` | 豆包写的 JSONL 有问题 | 按 `check_group.py` 的 errors 修 |
| `workspace_incomplete` | 还有切片没整理完 | 继续读，或用 `mark_slice.py` 补标记 |
| `process_exited` | 后台进程退出 | 重新跑；连续失败看日志 |
| `report_missing` | 报告文件不见了 | `start_daily.py --force` 重跑 |

采集有缺口时状态仍是 `ready`，但 `partial=true`、`warnings` 会写原因。要主动说明，不要只报喜。

## 常见提醒怎么解释

- **“只覆盖 19:15 以后”**：这台电脑的微信是那天 19:15 才登录的，登录前的群历史本机没有。要完整一天，采集电脑必须提前登录并一直在线。
- **“N 处低置信”**：向上滚动合并时有两段接不上，可能漏了少量消息；关键结论点“查看原文”核对。
- **“当前只配置 2 个群，预期 7 个”**：还有群没加进配置，统计不全。
- **“图片未落盘”**：采集时图片没截到；报告只能显示占位，不要说看到了原图。
- **“N 条线索置信度低”**：豆包自己标了不确定；点开原文人工确认。

## 命令速查

```bash
# 查状态
python scripts/status.py --date yesterday

# 采集 + 生成工作台（后台，约 15–25 分钟）
python scripts/start_daily.py --date yesterday

# 强制重采（会备份旧工作台）
python scripts/start_daily.py --date yesterday --force

# 标记一片读完
python scripts/mark_slice.py --work "<workdir>" --group g1 --slice part-001

# 校验一个群 / 全部
python scripts/check_group.py --work "<workdir>" --group g1
python scripts/check_group.py --work "<workdir>"

# 生成报告
python scripts/finalize_agent.py --work "<workdir>" --out "<date_dir>/report" --home "<home>"

# 打开报告
python scripts/open_report.py --date yesterday

# 今天到现在
python scripts/status.py --date today
python scripts/start_daily.py --date today
```

## 数据放在哪

默认在 `~/.rent-lead/wechat-group-daily/reports/<日期>/`：

```text
reports/2026-10-28/
  status.json          当前状态
  summary.json         给豆包读的摘要
  run.log              回捞日志
  work/                豆包工作台
    index.json
    README.md
    g1/
      digest.md
      index.json
      image_index.json
      slices/part-001.md
      notes/part-001.md
      leads.jsonl
      claims.jsonl
      images.jsonl
      progress.json
      open-questions.md
    g2/ ...
  probe/               原始采集、messages_*.jsonl、images/
  report/              report.html、facts.json、xlsx、images/
```

可用环境变量 `WECHAT_DAILY_HOME` 改数据目录。技能升级不会覆盖报告。

## 硬性规则

1. **只读**：不发送消息、不加好友、不改群名、不@人。
2. **采集期间不要操作微信**：探针会切群、滚动窗口；用户一动就会打断或串消息。
3. **一次只跑一个**：先查状态；`running` 时绝不重复启动。
4. **一个联系方式一条线索**：多条消息/图片里有多个联系方式，要拆。
5. **任何人都可以发线索**：不要只盯配置的发布人。
6. **领取由语义判断**：不只看“1”，但也不要凭空扩大；拿不准写 `uncertain`。
7. **图片必须逐张看**：不能把图片只当附件；图里的联系方式要读出来。
8. **不编证据**：`leads/claims/images` 只写 `message_uid`，原文由程序回填；不要自己生成大段聊天。
9. **不承诺结果**：只负责整理和展示，不说“已经加微信 / 已经联系 / 已经安排看房”。
10. **隐私**：原始聊天、联系方式、图片只留本机；不要上传公网、云盘、公开页面或外部聊天。
11. **不删原始数据**：`probe/`、`work/raw/`、`slices/` 都保留，方便复核。
