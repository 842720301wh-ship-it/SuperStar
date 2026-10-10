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

填空题支持单空与多空：自动读取空位数量和页面提交字段，AI 按空位顺序返回答案，再逐空写入。JSON 数组、换行及 `|`、`#` 分隔的多空答案均可识别；多空答案也支持分号分隔。答案中的逗号、空格、小数点会保留。缺少答案、空位数量不符或多空提交字段未识别时，只保存，不报完成。

简答题支持直接文本、JSON 字符串和分段答案数组，保留标点和换行，并读取页面的简答编辑器提交字段。AI 与 SiliconFlow 均提供简答提示词。空答案或无效答案仅保存，不报完成；提交后仍须通过平台完成状态核验，待教师批阅的题目可能尚未完成。

原有 `submit` 和 `cover_rate` 配置仍控制提交；填空题必须完整作答、简答题必须有有效正文才能提交，无需增加配置项。

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
