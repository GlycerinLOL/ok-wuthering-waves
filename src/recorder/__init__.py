"""跑轴记录: 把每一局的按键、切人、读屏、日志和画面记成时间线, 在「跑轴复盘」页里看。

原来是插件 axis_recorder (仓库 wk321425/ok-ww-axis-recorder), coco-ww 从 0.4.8 并进主程序,
在 config.py 的 trigger_tasks / custom_tabs 里直接注册。记录格式和反馈包格式跟插件版一致, 互相能打开。
挂钩方式没变: Recorder.install() 给读屏、冷却、切人这些函数套一层, 关掉任务就卸下。
"""

VERSION = '0.4.8'
