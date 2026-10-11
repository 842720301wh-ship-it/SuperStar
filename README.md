<p align="center">
  <a href="https://www.springing.top" target="blank">
    <img src="images/logo.png" alt="Logo" width="156" height="156">
  </a>
  <h2 align="center" style="font-weight: 600">Spring-Superstar</h2>
  <p align="center">
    学习通在线刷课脚本 超星 学习通 云端刷课
  </p>
</p>

本项目宗旨是帮助大学生们解放双手，根据教程操作后就可以完成刷课，简单易懂，仅需手机就可以使用

>如果本项目对希望各位同学们给本仓库点一个免费的Star或者给小春子点一个Follow 谢谢大家！

![截图](/images/star.png)

## 用前必读必读！
默认fork仓库之后是公开的状态，也就是说你的账号和密码是处于一个明文状态！！很危险！！建议把仓库权限改为私有

之前本仓库就因为明文账号密码泄露导致一些同学账号被盗拿来恶作剧(不得不说世界上还是坏人多)，故提前在此说明，希望大家周知

## 快速开始

<a href="https://blog.springing.top/p/20241119/" target="blank">点击我查看操作说明</a>

<a href="https://github.com/Samueli924/chaoxing" target="blank">灵感来源</a>

## 完成状态与题型支持

工作流成功表示章节处理成功且平台完成状态核验通过。测验仅保存、章节未开放或被跳过、重试耗尽、平台仍有未完成任务点时，程序会列出未完成章节并以失败状态退出。`notopen_action=continue` 可以继续处理其他章节，但跳过的章节仍计为未完成。

重跑时会读取平台最新状态，跳过已完成章节和任务点；视频从平台已记录的播放进度继续。任务卡片的 `isPassed` 支持布尔值、数字和字符串完成标记，`false`、`0` 不会误判为完成。作业答题前再次核对当前任务点，确认完成后不再请求 AI 或提交；收到“已批阅/不允许重复提交”时也须核验任务点完成，不能只凭提示报成功。章节失败或未开放重试耗尽前会刷新一次完成状态，最终仍核验整门课程。

GitHub 托管运行器的单个任务最多执行 6 小时。超时后的重跑可以跳过平台已完成的任务并续接视频进度，但不会自动延长此上限；仅保存和待批阅未通过的任务仍计为未完成。

填空题支持单空与多空：自动读取空位数量和页面提交字段，AI 按空位顺序返回答案，再逐空写入。JSON 数组、换行及 `|`、`#` 分隔的多空答案均可识别；多空答案也支持分号分隔。答案中的逗号、空格、小数点会保留。AI 未返回有效答案或空位数量不符时，逐空填入非空兜底答案。多空提交字段未识别时仍报未完成。

简答题支持直接文本、JSON 字符串和分段答案数组，保留标点和换行，并读取页面的简答编辑器提交字段。AI 与 SiliconFlow 均提供简答提示词。空答案或无效答案会使用非空兜底正文自动提交；提交后仍须通过平台完成状态核验，待教师批阅的题目可能尚未完成。

默认启用 `auto_submit=true`：主、备用模型均失败或题库查询异常时，选择题随机选项、判断题随机真假、填空题逐空填入兜底词语、简答题填入兜底正文，再自动提交，无需人工确认。兜底答案不计为 AI 命中，不写入题库答案缓存。`cover_rate=0.5` 仍保留用于配置和覆盖率记录，但在全自动模式下不拦截提交。设置 `auto_submit=false` 可恢复原有 `submit` 和 `cover_rate` 对提交的控制。

AI 和 SiliconFlow 答题在请求异常、空回复、解析失败或答案格式无效时，首次请求后最多重试 `ai_retry_times=3` 次，每个模型最多请求 4 次。主模型耗尽重试后切换 `fallback_model=LongCat-2.5-Preview`，沿用该提供商的接口和密钥；备用模型也遵守相同重试次数。模型名称按配置原样传给接口，需与服务端模型 ID 一致。重试前等待一秒，成功后直接返回；全部失败后自动填入兜底答案。命中缓存时不会请求 AI。连接预检查失败时自动继续，不再询问。

备用模型名称 `LongCat-2.5-Preview` 已按[美团 LongCat 官方 API 文档](https://longcat.chat/platform/docs/zh/)核对。当前工作流主模型为 `LongCat-2.0`，备用模型沿用官方 LongCat 接口。

当前工作流覆盖率设为 `0.5` 并开启全自动提交。「批量重跑历史任务」工作流逐条保留原 #24–#38 的 15 项任务，按各自历史提交读取账号、课程及主模型接口配置，再统一开启全自动提交、三次重试及备用模型，用当前代码执行，不做去重。每次最多运行三项，失败不会停止其他项；可在 Actions 页面手动运行该工作流再次执行整批任务。只有带 `[replay-batch]` 标记的提交自动启动批量运行，并跳过普通刷课工作流，避免额外启动当前账号任务。

离线回归检查：`python -m unittest discover -s tests -v`。测试直接载入源码中的目标函数，使用模拟接口验证状态传播和表单，不登录或提交真实课程。

本次工作流检查记录见 [任务检查报告](docs/workflow-audit-2026-10-10.md)。

## Star History
<a href="https://www.star-history.com/?repos=lispringing%2FSuperStar&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=lispringing/SuperStar&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=lispringing/SuperStar&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=lispringing/SuperStar&type=date&legend=top-left" />
 </picture>
</a>

## 赞助
>如果觉着代码对你有帮助，可以赞赏一下开发者

![截图](/images/reward.jpg)
