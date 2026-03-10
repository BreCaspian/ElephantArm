# myCobot320Pi Pick Teaching Project

This project is a terminal-based teaching tool for repetitive pick-and-place.

It supports:
- hand-guided trajectory recording
- gripper open/close events
- fixed start/end pose
- loop replay
- save/load JSON task file

## 1. Copy To Raspberry Pi

Copy folder:

`projects/320pi_pick_teacher`

to your Raspberry Pi (any path).

## 2. Environment

Install dependencies on Raspberry Pi:

```bash
pip3 install pymycobot pyserial
```

## 3. Run

```bash
python3 pick_teach_loop.py --port /dev/ttyAMA0 --baud 115200 --task pick_task_320pi.json
```

If `--port` is omitted, it will ask you to select from detected serial ports.

## 4. Recommended Teaching Workflow

1. Run script
2. `m` power on
3. `f` release all servos
4. Drag arm to cycle start pose, then press `1` (capture fixed START)
5. Press `r` to start recording
6. Hand-guide arm through pick trajectory
7. During teaching:
   - `k` to insert `gripper close`
   - `o` to insert `gripper open`
   - `w` to insert wait event
8. Press `c` to stop recording
9. Move arm to cycle end pose and press `2` (capture fixed END)
10. `s` save task
11. `t` run once
12. `y` run loop
13. `e` stop loop

## 5. Command List

- `h`: show help
- `p`: print current pose
- `z`: move to zero pose
- `f`: release servos
- `m`: power on servos
- `1`: capture fixed START
- `2`: capture fixed END
- `r`: start recording
- `c`: stop recording
- `o`: add gripper open event
- `k`: add gripper close event
- `w`: add wait event
- `x`: clear recorded items
- `s`: save task
- `l`: load task
- `t`: run once
- `y`: run loop
- `e`: stop loop
- `q`: quit

## 6. Notes

- Keep speed conservative first (40~60).
- Ensure no collision before loop run.
- If your gripper API differs by firmware/package version, the script tries:
  - `set_gripper_state(...)`
  - fallback `set_gripper_value(...)`

