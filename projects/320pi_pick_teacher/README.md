# myCobot 320Pi 固定点抓放工具

本项目用于 `myCobot 320 Pi` 的固定工位抓放任务。

“关键点示教 + 分段运动 + TCP 握手”：

- 抓取侧使用每个物品独立的关键点
- 等待位和放置侧当前为共享关键点
- 机械臂到等待位后向主机发送 `OK`
- 主机回复 `OK` 后，机械臂继续放置

相关文件：

- [pick_teach_loop.py](pick_teach_loop.py)
- [host_ok_server.py](host_ok_server.py)
- [pick_task_320pi.template.json](pick_task_320pi.template.json)

## 功能概览

当前已经实现：

- 单物品和多物品抓取
- 关键点示教
- 抓取、等待、放置的固定流程
- 夹爪开合控制
- TCP 握手
- 任务 JSON 保存和加载
- 主机端最小 TCP 测试脚本

当前未实现：

- 每个物品独立的放置点
- `sequence` 的编辑命令
- 复杂握手协议
- 自动重试和复杂故障恢复

## 执行流程

对每个物品，当前执行顺序固定为：

1. 夹爪打开
2. `MoveJ -> pick_approach`
3. `MoveL -> pick_pose`
4. 夹爪闭合
5. `MoveL -> pick_lift`
6. `MoveJ -> wait_pose`
7. 向主机发送 `OK`
8. 等待主机回复 `OK`
9. `MoveJ -> place_approach`
10. `MoveL -> place_pose`
11. 夹爪打开
12. `MoveL -> place_retreat`

## 点位说明

当前共使用 7 个关键点。

抓取侧为当前物品独立点：

- `pick_approach`
  抓取上方安全点。机械臂先到这里，再向抓取位接近。
- `pick_pose`
  实际抓取点。夹爪在这里闭合夹住物品。
- `pick_lift`
  抓取后抬升点。用于安全离开抓取区域。

共享点：

- `wait_pose`
  等待位。机械臂抓住物品后移动到这里，向主机发 `OK` 并等待主机回复。
- `place_approach`
  放置上方安全点。收到主机回复后，机械臂先到这里。
- `place_pose`
  实际放置点。夹爪在这里打开释放物品。
- `place_retreat`
  放置后回撤点。用于安全离开放置区域。

## 多物品说明

当前支持多个物品。

每个物品只拥有自己的抓取侧三点：

- `pick_approach`
- `pick_pose`
- `pick_lift`

所有物品共享以下点位：

- `wait_pose`
- `place_approach`
- `place_pose`
- `place_retreat`

因此，当前版本适合：

- 多个固定抓取位
- 一个共享等待位
- 一个共享放置位

如果后续需要“每个物品放到不同位置”，则需要再扩展任务模型。

## 环境准备

树莓派端安装依赖：

```bash
pip3 install pymycobot pyserial
```

电脑端运行主机脚本只需要 Python 标准库，不需要额外安装依赖。

## 树莓派端使用

示例：

```bash
python3 pick_teach_loop.py \
  --port /dev/ttyAMA0 \
  --baud 115200 \
  --task pick_task_320pi.json \
  --speed 70 \
  --linear-speed 40 \
  --settle-time 0.3 \
  --gripper-delay 0.3 \
  --host 192.168.253.131 \
  --host-port 25001 \
  --handshake-timeout 30
```

参数说明：

- `--port`
  机械臂串口。
- `--baud`
  串口波特率，默认 `115200`。
- `--task`
  任务 JSON 路径。
- `--speed`
  关节运动速度，范围 `1..100`。
- `--linear-speed`
  直线运动速度，范围 `1..100`。
- `--settle-time`
  每次运动后的停顿时间。
- `--gripper-delay`
  每次夹爪动作后的停顿时间。
- `--host`
  主机 IP。当前示例为电脑有线网卡地址 `192.168.253.131`。
- `--host-port`
  主机 TCP 端口。建议使用 `25001`。
- `--handshake-timeout`
  等待主机回复的超时时间，单位秒。
- `--send-message`
  发送给主机的字符串，默认 `OK`。
- `--recv-message`
  期望接收的字符串，默认 `OK`。
- `--no-network`
  禁用 TCP 握手，仅执行机械臂动作。
- `--debug`
  开启 `pymycobot` 调试输出。

## 主机端使用

主机端测试脚本：

- [host_ok_server.py](host_ok_server.py)

自动回复模式：

```bash
python host_ok_server.py --port 25001 --reply OK --reply-delay 5
```

含义：

- 监听本机 `25001` 端口
- 收到机械臂发来的 `OK`
- 等待 `5` 秒
- 回复 `OK`

手动回复模式：

```bash
python host_ok_server.py --port 25001 --manual
```

手动模式下：

- 直接回车：发送默认 `OK`
- 输入自定义文本：发送该文本
- 输入 `q`：关闭当前连接

主机端参数：

- `--host`
  监听地址，默认 `0.0.0.0`，表示监听本机所有网卡。
- `--port`
  监听端口。
- `--reply`
  默认回复内容，默认 `OK`。
- `--reply-delay`
  收到消息后延迟多少秒再回复，默认 `5.0`。
- `--manual`
  手动确认后再发送回复。

## 网络说明

当前推荐拓扑为：

- 树莓派网口直连电脑网口

当前示例网络：

- 电脑有线网卡：`192.168.253.131`
- 树莓派有线网卡：应配置为同网段地址，例如 `192.168.253.132`

注意：

- `--host` 要填电脑的 IP，不是 `0.0.0.0`
- `0.0.0.0` 只用于主机端脚本监听
- 树莓派和电脑必须在同一网段
- 电脑端必须先启动主机监听脚本

## 推荐联调顺序

1. 在电脑上启动主机端脚本

```bash
python host_ok_server.py --port 25001 --reply-delay 5
```

2. 在树莓派上确认能访问电脑 IP

```bash
ping 192.168.253.131
```

3. 在树莓派上启动机械臂脚本，并指定：

```bash
--host 192.168.253.131 --host-port 25001
```

4. 先用 `ping` 命令测试机械臂端到主机端的连接

5. 再执行 `t` 或 `y`

## CLI 命令

- `h`
  显示帮助菜单。
- `p`
  显示当前 `angles`、`coords`、`encoders`。
- `z`
  回零位。
- `f`
  释放舵机，便于手动拖动示教。
- `m`
  舵机上电。
- `item <name>`
  创建或切换当前物品，并自动加入 `sequence`。
- `seq`
  显示当前物品执行顺序。
- `v`
  显示任务摘要。
- `1`
  采集当前物品的 `pick_approach`。
- `2`
  采集当前物品的 `pick_pose`。
- `3`
  采集当前物品的 `pick_lift`。
- `4`
  采集共享点 `wait_pose`。
- `5`
  采集共享点 `place_approach`。
- `6`
  采集共享点 `place_pose`。
- `7`
  采集共享点 `place_retreat`。
- `o`
  夹爪打开。
- `k`
  夹爪闭合。
- `net`
  显示当前网络配置。
- `ping`
  立即尝试连接主机。
- `s`
  保存任务 JSON。
- `l`
  加载任务 JSON。
- `x`
  清空整个任务。
- `t`
  单次执行。
- `y`
  循环执行。
- `e`
  请求停止循环。
- `q`
  退出程序。

## 单物品示教步骤

1. `m` 上电
2. `f` 释放舵机
3. 手动拖动并采集共享点：
   - `4` -> `wait_pose`
   - `5` -> `place_approach`
   - `6` -> `place_pose`
   - `7` -> `place_retreat`
4. 采集当前物品抓取点：
   - `1` -> `pick_approach`
   - `2` -> `pick_pose`
   - `3` -> `pick_lift`
5. `v` 检查点位是否齐全
6. `s` 保存
7. `t` 单次执行

## 多物品示教步骤

如果有两个物品：

1. 先采一次共享点：
   - `4`
   - `5`
   - `6`
   - `7`
2. 采 `item_1`：

```text
item item_1
1
2
3
```

3. 采 `item_2`：

```text
item item_2
1
2
3
```

4. 查看顺序：

```text
seq
```

5. 保存并执行：

```text
s
t
```

默认 `sequence` 形如：

```json
["item_1", "item_2"]
```

## 任务文件格式

当前任务文件版本为 `task_version = 3`。

参考模板：

- [pick_task_320pi.template.json](pick_task_320pi.template.json)

基本结构：

```json
{
  "task_version": 3,
  "network": {},
  "params": {},
  "shared_points": {},
  "items": {
    "item_1": {
      "pick_approach": {},
      "pick_pose": {},
      "pick_lift": {}
    }
  },
  "sequence": ["item_1"]
}
```

每个点位保存：

- `angles`
- `coords`
- `encoders`
- `captured_at`

## 日志说明

当前机械臂端已输出关键日志，例如：

```text
MoveJ -> shared.wait_pose @ speed=70
Handshake start: host=192.168.253.131:25001, timeout=30.0s
Handshake reconnecting to host...
Handshake send: 'OK'
Handshake waiting reply...
Handshake reply: 'OK'
Handshake OK.
```

这些日志可用于判断：

- 是否到达等待位
- 是否成功重连主机
- 是否已经发出 `OK`
- 是否正在阻塞等待
- 是否已经收到主机回复

## 常见问题

### 1. `Failed to connect to host`

通常表示：

- 主机端脚本没有启动
- 端口不对
- 防火墙拦截
- 树莓派和电脑不在同一网段

### 2. `Host closed the socket`

表示：

- TCP 连接建立过
- 但主机端先关闭了连接

### 3. `Broken pipe`

表示：

- 连接已经被对方关闭
- 本端还在往这条旧连接里发送数据

当前机械臂端已经改成每次握手前强制重连，以减少该问题。

### 4. 主机端 `WinError 10013`

表示当前端口在 Windows 上无法监听。建议改用更高的端口，例如 `25001`。

## 当前建议

- 主机端优先使用 `25001`
- 主机端先启动，再启动树莓派端
- 联调时先不用 `--manual`
- 先用自动回 `OK` 跑通整链路
- 确认通信稳定后再做更复杂的控制逻辑
