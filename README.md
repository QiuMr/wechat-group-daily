# 微信群日报

只读回捞微信群历史消息，由豆包逐条读、逐张看图，整理成“一条联系方式一条线索”的群日报，自动统计去重客户、丢客员和客服领取，生成一页式 HTML 和 Excel；配置发布员月目标后，还会自动生成月度目标进度看板。

给豆包/WorkBuddy 用时看 `SKILL.md`；这份 README 是给安装和维护的人看的。

GitHub：<https://github.com/QiuMr/wechat-group-daily>

## 环境要求

- Windows 10/11
- 微信电脑版 4.1.8.x（4.1.12+ 免费版挂不上）
- 能上网：首次安装要从国内镜像下载 Python 3.12 和依赖
- 采集电脑在目标日期之前就登录微信，并保持在线
- 豆包工作模式能读写本地文件、打开本地图片；云端沙盒不行

## 安装

### 从 GitHub 安装

让豆包从仓库安装最新版：

```text
从 https://github.com/QiuMr/wechat-group-daily 安装这个技能
```

或者手动：

```bash
git clone https://github.com/QiuMr/wechat-group-daily.git
```

### 从 zip 安装

解压 `wechat-group-daily.zip` 到本机技能目录。

### 初始化（豆包自动）

让豆包在技能目录运行：

```bat
setup_silent.bat
```

它会用国内镜像自动安装 Python 3.12（本机没有时）和依赖，约 5–10 分钟，不暂停、不需要管理员。最后一行的 JSON 里 `ok:true` 即完成。

人工安装可以双击 `setup.bat`，逻辑相同，只是结尾会暂停。

默认源：Python 安装包来自 npmmirror / 华为云（校验签名），依赖来自清华 PyPI；失败自动换阿里云。手动换源：

```bat
setup_silent.bat -PipMirror https://mirrors.aliyun.com/pypi/simple/
```

装完后编辑 `config\groups.json`，填 7 个群的完整群名，再登录微信。

## 新版流程：豆包整理，Python 辅助

```text
start_daily.py
  → 只读回捞
  → prepare_workspace.py
  → work/<日期>/<群>/ 切片、图片索引、空账本
  → 豆包逐片读、逐张看图
  → leads.jsonl / claims.jsonl / images.jsonl
  → check_group.py 校验
  → finalize_agent.py 生成 HTML/Excel/摘要
  → 有 config/targets.json 时，重算当月并生成目标进度看板
```

Python 不判断线索和领取：

- 一个联系方式一条线索；
- 任何人发的都算线索；
- 连续丢 2–4 个客户算一批，后面一个“1”默认领整批；两个人各扣 1，就算两个人都领了整批；
- 扣 1 的语义范围由豆包判断；
- 图片是不是线索由豆包看图判断。

Python 只做机械统计（`scripts/analytics.py`）：

- 按联系方式去重客户，归到最早一次发布；
- 领取量按“客户 × 客服”算：同一客户两个客服领算 2 次，一个“1”领一批 3 个客户算 3 次；
- 每个丢客员的客户数、被领数、领取率、主要领取人；
- 每个客服的领取客户数、占比、最常领谁、偏好；
- “B 常领 C 的客户”组合占比。

## 发布员目标进度（可选）

配好月目标后，每次生成日报会自动重算当月，并多出一页目标看板：

- 团队完成率、时间进度、进度差、月底预测；
- 每个发布员的月目标、日均目标、今日、月累计、完成率、距目标、剩余日均、状态；
- 今日产出 vs 日均目标、累计趋势、每日热力图；
- `目标进度_YYYY-MM.xlsx` 四张表：团队、发布员目标、每日明细、口径与提醒。

配置：把 `config/targets.example.json` 复制为 `config/targets.json`，填发布员和月目标：

```json
{
  "days": 30,
  "publishers": [
    {"name": "张三", "aliases": ["小张", "张"], "monthly_target": 1600},
    {"name": "李四", "aliases": ["小李"], "monthly_target": 1400}
  ]
}
```

- 日目标 = 月目标 ÷ `days`（默认 30）；时间进度 = 已过天数 ÷ 30，封顶 100%；2 月等短月要按自然月进度，把 `days` 改成 28/29；
- 同一客户同月归**第一次发布**的发布员，后面换人重发不重复计数；
- 月累计按当月所有 `facts.json` 重扫，重跑某天会覆盖，不会重复累加；
- 没有配置、或配置里的 `month` 和统计月份不一致时自动跳过，不影响日报；
- 第一次没有配置时，豆包会在日报后问一次要不要设目标；说“不用”会写 `config/targets.off`，以后不再反复问，想设时说“设置发布员目标”即可；
- 对不上名字的发布员列进“未设目标”，只展示产出，不计团队完成率。

目标里的“完成”是**发布的去重客户数**，不是领取量，也不是实际加微/拉群/开单；实际工作量仍以主管日报和核对群截图为准。

## 手动使用

```bash
# 查状态
.venv\Scripts\python.exe scripts\status.py --date yesterday

# 采集 + 生成工作台（后台，约 30-90 分钟）
.venv\Scripts\python.exe scripts\start_daily.py --date yesterday

# 豆包整理完一个群后校验
.venv\Scripts\python.exe scripts\check_group.py --work "<workdir>" --group g1

# 全部整理完后生成报告
.venv\Scripts\python.exe scripts\finalize_agent.py --work "<workdir>" --out "<date_dir>\report" --home "<home>"

# 打开报告
.venv\Scripts\python.exe scripts\open_report.py --date yesterday

# 手动重算目标看板 / 打开目标看板
.venv\Scripts\python.exe scripts\targets.py --date yesterday
.venv\Scripts\python.exe scripts\open_report.py --date yesterday --targets
```

采集期间不要操作微信。探针会切群、向上滚动，但不会发消息。

## 工作台

默认目录：`C:\Users\<用户>\.rent-lead\wechat-group-daily\reports\<日期>\work\`

每个群一个子目录：

```text
g1/
  digest.md          群档案和整理口径
  index.json         切片清单
  image_index.json   图片 uid、绝对路径、前后文
  slices/part-001.md 原始消息切片
  notes/part-001.md  豆包阅读笔记
  leads.jsonl        线索卡
  claims.jsonl       领取记录
  images.jsonl       图片判断
  progress.json      进度
  open-questions.md  拿不准的问题
```

豆包按切片顺序读，边读边追加，不要一次吞 7 个群。

## 输出

默认目录：`C:\Users\<用户>\.rent-lead\wechat-group-daily\reports\<日期>\`

- `report.html`：一页式日报
- `群日报_<日期>.xlsx`：线索明细、客户去重、丢客员、客服领取、领取关系、领取记录、异常、数据说明
- `summary.json`：给 AI 读的摘要
- `facts.json`：完整结构化数据，含证据和图片绝对路径
- `目标进度.html` / `目标进度_YYYY-MM.xlsx` / `target_facts.json`：配置目标后多出的月度看板
- `work/`：豆包工作台
- `probe/`：原始采集，排查缺口用

## 常见问题

**只能拉到晚上 7 点以后？**

这台电脑的微信是晚上 7 点才登录的。wxauto 只能读本机登录后的本地记录，手机/服务器上的更早历史读不到。要完整一天，采集电脑必须提前登录并保持在线。

**某个群显示“未采集”？**

群名不一致，或微信里没有这个群。复制完整群名（含表情）重新填。

**客户数为什么比线索数少？**

同一个号码/微信号在多个群或同一天被重复发布，按联系方式去重后只算一个客户，归到最早一次发布；其他发布在客户表里记“重复发布”。

**一个客户被两个客服扣 1，算几次领取？**

算 2 次。领取量按“客户 × 客服”计算；一个“1”领走一批 3 个客户，算 3 次领取、1 条领取消息。

**目标看板的完成数为什么和领取量、加微量对不上？**

目标看板统计的是发布员发布的去重客户数；领取量是“客户 × 客服”，实际加微、拉群、带看、开单又是另一套数。三个口径不要混用。目标完成率只说明发布量跟没跟上目标，不代表实际工作量。

**同一个客户被两个发布员发，算谁的？**

算同月第一个发布的人。后面换人重发不再重复计数，避免一个客户被算两次。

**时间进度为什么是 96.7%，不是 29/31？**

目标按 30 天口径算：日目标 = 月目标 ÷ 30，时间进度 = 已过天数 ÷ 30，封顶 100%。第 29 天就是 29 ÷ 30 = 96.7%。

**缺一天日报，目标看板会怎样？**

缺的那天不按 0 计，热力图标“缺”，累计按已知日期算，但会提示“可能偏低”。补跑那天的日报后，重算目标就会补上。

**第一次没设目标，豆包会主动引导吗？**

会。第一次报告完成后，豆包会问一次要不要设置发布员月目标；同意后按名单和月目标引导配置，不需要你手写 JSON。说“不用”会在本机写 `config/targets.off`，以后不再追问；想设时直接说“设置发布员目标”。

**配置了目标，但看板没出来？**

检查 `config/targets.json` 是否存在、JSON 是否写对、`month` 是否和统计月份一致。没有配置或月份不一致时，finalize 会自动跳过，不影响日报。

**图片只有占位？**

采集时图片没截到。看 `work/<群>/image_index.json` 的 `saved`；不要把它当成原图。

**豆包整理到一半断了怎么办？**

看 `work/<群>/progress.json` 的 `done_slices`，从没标记的那一片继续；工作台文件就是进度。

**安装时下载 Python 或依赖很慢/失败？**

默认已经走国内镜像：Python 用 npmmirror / 华为云，依赖用清华 PyPI，失败自动换阿里云。仍然失败时手动指定：

```bat
setup_silent.bat -PipMirror https://mirrors.aliyun.com/pypi/simple/
```

提示 `python_missing` 说明镜像都不可达：先检查网络，或手动装 Python 3.12 后重跑。

**安装需要管理员吗？**

不需要。Python 装到当前用户目录，依赖装在技能的 `.venv` 里，不写系统目录。

**某个群只覆盖到今天上午、没扫到昨天？**

这个群窗口小、消息密，滚动到单群上限（默认 2000 轮）还没翻到 00:00。把 `config/groups.json` 的 `probe.max_rounds` 调大（如 4000）再跑一次；仍然到不了才是本机缓存或入群边界。

**微信 4.1.12 以上能跑吗？**

免费版 wxauto4 41.1.7 挂不上 4.1.12+。需要降级到 4.1.8.x，或买付费版/换方案。

## 隐私

原始聊天、联系方式、图片只留本机，不上传。报告和 Excel 默认不打码，只在本机打开；发给谁由用户决定。GitHub 仓库不含真实群名、联系方式、聊天记录和图片。

发布员姓名、月目标同样敏感：真实配置写在 `config/targets.json`，已被 `.gitignore` 排除，不会进入仓库或技能 zip；`config/targets.off` 只在客户本机记录“暂不设置”，同样不上传。
