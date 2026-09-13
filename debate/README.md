# 双AI高真实性辩论攻防模拟器 v5.1 — CCSwitch

## 这版的关键修复

1. 默认不再调用第二个 DeepSeek 模型做 Committer，避免 CCSwitch 中不存在的 `deepseek-v4-flash` 导致失败。状态提交默认使用本地 Python 单调更新。
2. A/B 仍然是独立上下文：A 只获得 PUBLIC_STATE + A_PRIVATE；B 只获得 PUBLIC_STATE + B_PRIVATE。
3. 角色 Prompt 改为“真实辩手”风格：不要求一次讲完所有定义、边界和证明责任，优先回应对手上一句话，允许有限让步与路线变化。
4. Windows 输出使用 UTF-8 + errors=replace，避免 GBK 解码崩溃。

## Windows CMD

### 这是示例，更改需要填写的内容后可直接复制进CMD

```bat
cd /d debate文件夹所在地址（此处需要填写）
python runtime\doctor.py
python runtime\runner.py --mode ccswitch --topic "AI是否会降低人的创造力？" --a-side "反方" --b-side "正方" --max-units 1 --effort low
```

先跑 1 个有效单元。确认 A/B 都有真实回应后，再把 `--max-units` 改为 3、10、50。

### 如果再次报 `unrecognized_model`

先不要加 `--model` 参数。CCSwitch 负责当前会话模型选择；本运行器默认不覆盖它。

### Committer

默认：`python`。
需要实验第二模型提交器时再使用：

```bat
python runtime\runner.py --mode ccswitch --committer-mode llm ...
```

但第一次测试不推荐 `llm`。
