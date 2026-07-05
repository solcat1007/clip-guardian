# ClipGuardian
> 剪辑项目自动备份守护工具

## 功能
- 实时监控 PR / AE / 剪映专业版进程运行状态
- 定时自动备份指定文件夹为 ZIP 压缩包
- 备份历史列表管理，支持恢复和删除旧备份
- 配置文件持久化存储，一次配置长期生效

## 使用方法
```bash
# 安装依赖
install.bat
# 或手动执行
pip install -r requirements.txt

# 启动
python clip_guardian.py
```
启动后设置监控目标文件夹和备份间隔，工具会在检测到剪辑软件运行时自动备份。
