# AI Project Studio

**简体中文** | [English](README.en.md)

AI Project Studio 是一个由制作人工作流驱动的 Codex 插件，用于持续推进游戏和 Web 应用项目，而不把聊天记录当作项目记忆。

它将项目事实保存在代码仓库中，只允许执行已经批准的工作，明确标注交付物完成度，并能在新的 Codex 任务和临时子代理之间恢复项目上下文。

## 提供的能力

- 探索、验证、构建、发布和运营阶段
- 游戏与 Web 应用项目配置
- 保存在项目内的目标、决策、路线图、待办事项，以及试玩或产品记录
- 有明确边界且需要批准规格的工作项
- 从占位内容到最终实装效果的交付物等级
- 精简的上下文恢复摘要和暂停检查点
- 由用户掌控的批准和最终验收记录
- 可重复验证的状态检查与项目生命周期命令

这套工作流刻意避免虚构常驻团队、穷举式检查清单，以及自动膨胀的流程。

## 环境要求

- 支持插件的 Codex
- Python 3.10 或更高版本

## 本地安装

将仓库克隆到个人插件源码目录：

```bash
git clone https://github.com/bigs-ad/ai-project-studio.git ~/plugins/ai-project-studio
```

然后让 Codex 把本地 `ai-project-studio` 目录加入个人插件市场并安装。安装完成后，应当可以使用 `$ai-project-studio:manage-project-studio` skill。

## 启动项目

在一个游戏或 Web 应用代码仓库中打开 Codex，并提供一份简短的项目说明：

```text
使用 $ai-project-studio:manage-project-studio 启动这个游戏项目。

项目名称：[名称]
项目负责人：[负责人标识]
初始想法：[粗略想法]
```

第一次响应会停留在探索阶段：不实现、不委派；区分事实、假设和未知项；指出当前最大的一个风险、推荐的唯一下一步，并提出一个最重要的问题。

## 恢复上下文

开始后续任务时，可以这样要求：

```text
使用 $ai-project-studio:manage-project-studio 恢复这个项目，并推荐唯一的下一步。
```

该 skill 会验证项目状态、生成精简摘要，并且只读取当前决策所需的文件。

## 命令行工具

确定性的状态管理工具位于：

```text
skills/manage-project-studio/scripts/studio.py
```

使用示例：

```bash
python3 skills/manage-project-studio/scripts/studio.py profiles
python3 skills/manage-project-studio/scripts/studio.py validate /path/to/project
python3 skills/manage-project-studio/scripts/studio.py brief /path/to/project
```

运行 `--help` 可以查看生命周期、门禁、工作项、修复、负责人和检查点相关命令。

## 开发

运行回归测试：

```bash
python3 -m unittest discover \
  -s skills/manage-project-studio/scripts/tests \
  -v
```

## 适用范围

当前配置支持游戏和 Web 应用。其他项目类型需要专门的项目配置，不会被默认视为同类项目处理。

## 许可证

[MIT](LICENSE)
