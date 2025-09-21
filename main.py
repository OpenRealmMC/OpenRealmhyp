import json
import os
import subprocess
import psutil
import signal
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uvicorn
import time
import threading

app = FastAPI(
    title="ZBProxy管理API",
    description="用于管理ZBProxy配置和控制其运行状态的API",
    version="1.0.0"
)

# 配置文件路径
CONFIG_FILE = "ZBProxy.json"
ZBPROXY_EXECUTABLE = "ZBProxy-linux-amd64-v1"

# 全局变量存储进程信息
zbproxy_process = None
process_lock = threading.Lock()

class ConfigUpdateRequest(BaseModel):
    """配置更新请求模型"""
    path: str  # JSON路径，如 "Services.0.Listen" 或 "Log.Level"
    value: Any  # 新值

class ServiceConfig(BaseModel):
    """服务配置模型"""
    Name: str
    Listen: int
    IPAccess: Dict[str, Any] = {"Mode": ""}
    Outbound: Dict[str, Any] = {"Type": ""}

class OutboundConfig(BaseModel):
    """出站配置模型"""
    Name: str
    TargetAddress: str
    TargetPort: int
    Minecraft: Optional[Dict[str, Any]] = None
    ProxyOptions: Optional[Dict[str, Any]] = None

def load_config() -> Dict[str, Any]:
    """加载ZBProxy配置文件"""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="配置文件未找到")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"配置文件格式错误: {str(e)}")

def save_config(config: Dict[str, Any]) -> None:
    """保存ZBProxy配置文件"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存配置文件失败: {str(e)}")

def get_nested_value(data: Dict[str, Any], path: str) -> Any:
    """根据路径获取嵌套字典中的值"""
    keys = path.split('.')
    current = data
    
    for key in keys:
        if key.isdigit():
            # 处理数组索引
            key = int(key)
            if isinstance(current, list) and 0 <= key < len(current):
                current = current[key]
            else:
                raise HTTPException(status_code=400, detail=f"无效的数组索引: {key}")
        elif isinstance(current, dict) and key in current:
            current = current[key]
        else:
            raise HTTPException(status_code=400, detail=f"路径不存在: {path}")
    
    return current

def set_nested_value(data: Dict[str, Any], path: str, value: Any) -> None:
    """根据路径设置嵌套字典中的值"""
    keys = path.split('.')
    current = data
    
    for i, key in enumerate(keys[:-1]):
        if key.isdigit():
            key = int(key)
            if isinstance(current, list) and 0 <= key < len(current):
                current = current[key]
            else:
                raise HTTPException(status_code=400, detail=f"无效的数组索引: {key}")
        elif isinstance(current, dict) and key in current:
            current = current[key]
        else:
            raise HTTPException(status_code=400, detail=f"路径不存在: {path}")
    
    last_key = keys[-1]
    if last_key.isdigit():
        last_key = int(last_key)
        if isinstance(current, list) and 0 <= last_key < len(current):
            current[last_key] = value
        else:
            raise HTTPException(status_code=400, detail=f"无效的数组索引: {last_key}")
    elif isinstance(current, dict):
        current[last_key] = value
    else:
        raise HTTPException(status_code=400, detail=f"无法设置值到路径: {path}")

def find_zbproxy_process() -> Optional[psutil.Process]:
    """查找ZBProxy进程"""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['name'] and ZBPROXY_EXECUTABLE.lower() in proc.info['name'].lower():
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None

@app.get("/")
async def root():
    """根路径"""
    return {"message": "ZBProxy管理API", "version": "1.0.0"}

@app.get("/config")
async def get_config():
    """获取当前配置"""
    config = load_config()
    return {"success": True, "config": config}

@app.get("/config/{path:path}")
async def get_config_value(path: str):
    """获取指定路径的配置值"""
    config = load_config()
    value = get_nested_value(config, path)
    return {"success": True, "path": path, "value": value}

@app.put("/config")
async def update_config_value(request: ConfigUpdateRequest):
    """更新指定路径的配置值"""
    config = load_config()
    
    # 验证路径是否存在
    try:
        get_nested_value(config, request.path)
    except HTTPException:
        pass  # 如果路径不存在，我们仍然可以尝试设置它
    
    # 设置新值
    set_nested_value(config, request.path, request.value)
    save_config(config)
    
    return {
        "success": True, 
        "message": f"配置已更新: {request.path} = {request.value}"
    }

@app.post("/config/service")
async def add_service(service: ServiceConfig):
    """添加新的服务配置"""
    config = load_config()
    
    if "Services" not in config:
        config["Services"] = []
    
    # 检查服务名是否已存在
    for existing_service in config["Services"]:
        if existing_service.get("Name") == service.Name:
            raise HTTPException(status_code=400, detail=f"服务 '{service.Name}' 已存在")
    
    config["Services"].append(service.dict())
    save_config(config)
    
    return {"success": True, "message": f"服务 '{service.Name}' 已添加"}

@app.delete("/config/service/{service_name}")
async def delete_service(service_name: str):
    """删除指定的服务配置"""
    config = load_config()
    
    if "Services" not in config:
        raise HTTPException(status_code=404, detail="没有找到服务配置")
    
    original_length = len(config["Services"])
    config["Services"] = [s for s in config["Services"] if s.get("Name") != service_name]
    
    if len(config["Services"]) == original_length:
        raise HTTPException(status_code=404, detail=f"服务 '{service_name}' 未找到")
    
    save_config(config)
    return {"success": True, "message": f"服务 '{service_name}' 已删除"}

@app.post("/config/outbound")
async def add_outbound(outbound: OutboundConfig):
    """添加新的出站配置"""
    config = load_config()
    
    if "Outbounds" not in config:
        config["Outbounds"] = []
    
    # 检查出站名是否已存在
    for existing_outbound in config["Outbounds"]:
        if existing_outbound.get("Name") == outbound.Name:
            raise HTTPException(status_code=400, detail=f"出站 '{outbound.Name}' 已存在")
    
    outbound_dict = outbound.dict()
    if outbound_dict.get("Minecraft") is None:
        outbound_dict["Minecraft"] = {
            "EnableHostnameRewrite": True,
            "OnlineCount": {"Max": 20, "Online": -1, "EnableMaxLimit": False},
            "HostnameAccess": {"Mode": ""},
            "NameAccess": {"Mode": ""},
            "PingMode": "",
            "MotdFavicon": "{DEFAULT_MOTD}",
            "MotdDescription": "§d{NAME}§e, provided by §a§o{INFO}§r\n§c§lProxy for §6§n{HOST}:{PORT}§r"
        }
    
    if outbound_dict.get("ProxyOptions") is None:
        outbound_dict["ProxyOptions"] = {"Type": ""}
    
    config["Outbounds"].append(outbound_dict)
    save_config(config)
    
    return {"success": True, "message": f"出站 '{outbound.Name}' 已添加"}

@app.delete("/config/outbound/{outbound_name}")
async def delete_outbound(outbound_name: str):
    """删除指定的出站配置"""
    config = load_config()
    
    if "Outbounds" not in config:
        raise HTTPException(status_code=404, detail="没有找到出站配置")
    
    original_length = len(config["Outbounds"])
    config["Outbounds"] = [o for o in config["Outbounds"] if o.get("Name") != outbound_name]
    
    if len(config["Outbounds"]) == original_length:
        raise HTTPException(status_code=404, detail=f"出站 '{outbound_name}' 未找到")
    
    save_config(config)
    return {"success": True, "message": f"出站 '{outbound_name}' 已删除"}

@app.post("/fix-permissions")
async def fix_permissions():
    """修复ZBProxy可执行文件权限"""
    if not os.path.exists(ZBPROXY_EXECUTABLE):
        raise HTTPException(
            status_code=404, 
            detail=f"ZBProxy可执行文件未找到: {ZBPROXY_EXECUTABLE}"
        )
    
    try:
        # 获取当前权限
        current_permissions = os.stat(ZBPROXY_EXECUTABLE).st_mode
        
        # 设置执行权限 (755: rwxr-xr-x)
        os.chmod(ZBPROXY_EXECUTABLE, 0o755)
        
        # 验证权限设置
        new_permissions = os.stat(ZBPROXY_EXECUTABLE).st_mode
        
        return {
            "success": True,
            "message": f"已修复 {ZBPROXY_EXECUTABLE} 的执行权限",
            "old_permissions": oct(current_permissions)[-3:],
            "new_permissions": oct(new_permissions)[-3:],
            "file_path": os.path.abspath(ZBPROXY_EXECUTABLE)
        }
        
    except PermissionError:
        raise HTTPException(
            status_code=403, 
            detail="权限不足，无法修改文件权限。请以管理员身份运行。"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"修复权限时出错: {str(e)}"
        )

@app.get("/status")
async def get_status():
    """获取ZBProxy运行状态"""
    global zbproxy_process
    
    with process_lock:
        # 检查我们记录的进程是否还在运行
        if zbproxy_process and zbproxy_process.poll() is None:
            return {
                "success": True,
                "status": "running",
                "pid": zbproxy_process.pid,
                "message": "ZBProxy正在运行"
            }
        
        # 尝试查找系统中的ZBProxy进程
        proc = find_zbproxy_process()
        if proc:
            zbproxy_process = None  # 清除我们的记录，因为这个进程不是我们启动的
            return {
                "success": True,
                "status": "running",
                "pid": proc.pid,
                "message": "ZBProxy正在运行（外部启动）"
            }
        
        zbproxy_process = None
        return {
            "success": True,
            "status": "stopped",
            "message": "ZBProxy未运行"
        }

@app.post("/start")
async def start_zbproxy():
    """启动ZBProxy"""
    global zbproxy_process
    
    with process_lock:
        # 检查是否已经在运行
        if zbproxy_process and zbproxy_process.poll() is None:
            return {
                "success": False,
                "message": "ZBProxy已在运行",
                "pid": zbproxy_process.pid
            }
        
        # 检查系统中是否有ZBProxy进程
        proc = find_zbproxy_process()
        if proc:
            return {
                "success": False,
                "message": f"ZBProxy已在运行（PID: {proc.pid}）"
            }
        
        # 检查可执行文件是否存在
        if not os.path.exists(ZBPROXY_EXECUTABLE):
            raise HTTPException(
                status_code=404, 
                detail=f"ZBProxy可执行文件未找到: {ZBPROXY_EXECUTABLE}"
            )
        
        # 确保可执行文件有执行权限
        try:
            current_permissions = os.stat(ZBPROXY_EXECUTABLE).st_mode
            if not (current_permissions & 0o111):  # 检查是否有任何执行权限
                os.chmod(ZBPROXY_EXECUTABLE, current_permissions | 0o755)
                print(f"已为 {ZBPROXY_EXECUTABLE} 添加执行权限")
        except Exception as e:
            print(f"设置执行权限时出现警告: {str(e)}")
        
        try:
            # 打开日志文件
            log_file = open("out.log", "w", encoding="utf-8")
            
            # 启动ZBProxy，将输出重定向到日志文件
            executable_path = os.path.abspath(ZBPROXY_EXECUTABLE)
            zbproxy_process = subprocess.Popen(
                [executable_path],
                cwd=os.getcwd(),
                stdout=log_file,
                stderr=subprocess.STDOUT,  # 将错误输出也重定向到stdout
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )
            
            # 将日志文件句柄保存到进程对象中，以便后续关闭
            zbproxy_process.log_file = log_file
            
            # 等待一小段时间确保进程启动
            time.sleep(1)
            
            if zbproxy_process.poll() is None:
                return {
                    "success": True,
                    "message": "ZBProxy启动成功",
                    "pid": zbproxy_process.pid
                }
            else:
                zbproxy_process = None
                raise HTTPException(status_code=500, detail="ZBProxy启动失败")
                
        except FileNotFoundError as e:
            zbproxy_process = None
            raise HTTPException(
                status_code=404, 
                detail=f"ZBProxy可执行文件未找到或无法执行: {ZBPROXY_EXECUTABLE}。请检查文件是否存在且有执行权限。"
            )
        except PermissionError as e:
            zbproxy_process = None
            raise HTTPException(
                status_code=403, 
                detail=f"没有权限执行ZBProxy: {ZBPROXY_EXECUTABLE}。请检查文件权限或以管理员身份运行。"
            )
        except Exception as e:
            zbproxy_process = None
            raise HTTPException(status_code=500, detail=f"启动ZBProxy时出错: {str(e)}")

@app.post("/stop")
async def stop_zbproxy():
    """停止ZBProxy"""
    global zbproxy_process
    
    with process_lock:
        stopped_processes = []
        
        # 停止我们启动的进程
        if zbproxy_process and zbproxy_process.poll() is None:
            try:
                if os.name == 'nt':
                    zbproxy_process.terminate()
                else:
                    zbproxy_process.send_signal(signal.SIGTERM)
                
                # 等待进程结束
                try:
                    zbproxy_process.wait(timeout=5)
                    stopped_processes.append(zbproxy_process.pid)
                except subprocess.TimeoutExpired:
                    zbproxy_process.kill()
                    zbproxy_process.wait()
                    stopped_processes.append(zbproxy_process.pid)
                
                # 关闭日志文件
                if hasattr(zbproxy_process, 'log_file') and zbproxy_process.log_file:
                    try:
                        zbproxy_process.log_file.close()
                    except:
                        pass
                
            except Exception as e:
                pass
            finally:
                zbproxy_process = None
        
        # 查找并停止其他ZBProxy进程
        proc = find_zbproxy_process()
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=5)
                stopped_processes.append(proc.pid)
            except (psutil.TimeoutExpired, psutil.AccessDenied):
                try:
                    proc.kill()
                    stopped_processes.append(proc.pid)
                except:
                    pass
        
        if stopped_processes:
            return {
                "success": True,
                "message": f"ZBProxy已停止 (PID: {', '.join(map(str, stopped_processes))})"
            }
        else:
            return {
                "success": True,
                "message": "ZBProxy未在运行"
            }

@app.post("/restart")
async def restart_zbproxy():
    """重启ZBProxy"""
    # 先停止
    stop_result = await stop_zbproxy()
    
    # 等待一下确保进程完全停止
    time.sleep(2)
    
    # 再启动
    start_result = await start_zbproxy()
    
    if start_result["success"]:
        return {
            "success": True,
            "message": "ZBProxy重启成功",
            "pid": start_result.get("pid")
        }
    else:
        return {
            "success": False,
            "message": f"重启失败: {start_result.get('message', '未知错误')}"
        }

@app.get("/logs")
async def get_logs():
    """获取日志信息和ZBProxy状态监听"""
    global zbproxy_process
    
    # 获取ZBProxy状态
    status_info = {
        "status": "stopped",
        "pid": None,
        "running_time": None,
        "message": "ZBProxy未运行"
    }
    
    with process_lock:
        # 检查我们记录的进程是否还在运行
        if zbproxy_process and zbproxy_process.poll() is None:
            try:
                proc = psutil.Process(zbproxy_process.pid)
                status_info = {
                    "status": "running",
                    "pid": zbproxy_process.pid,
                    "running_time": time.time() - proc.create_time(),
                    "cpu_percent": proc.cpu_percent(),
                    "memory_info": proc.memory_info()._asdict(),
                    "message": "ZBProxy正在运行"
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                zbproxy_process = None
                status_info["message"] = "ZBProxy进程已结束"
        else:
            # 尝试查找系统中的ZBProxy进程
            proc = find_zbproxy_process()
            if proc:
                try:
                    status_info = {
                        "status": "running",
                        "pid": proc.pid,
                        "running_time": time.time() - proc.create_time(),
                        "cpu_percent": proc.cpu_percent(),
                        "memory_info": proc.memory_info()._asdict(),
                        "message": "ZBProxy正在运行（外部启动）"
                    }
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
    
    # 读取日志文件
    log_files = ["out.log", "zbproxy.log", "error.log", "access.log"]
    logs = {}
    
    for log_file in log_files:
        if os.path.exists(log_file):
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    # 只读取最后200行
                    lines = f.readlines()
                    logs[log_file] = {
                        "lines": lines[-200:] if len(lines) > 200 else lines,
                        "total_lines": len(lines),
                        "file_size": os.path.getsize(log_file)
                    }
            except Exception as e:
                logs[log_file] = {
                    "lines": [f"读取日志文件失败: {str(e)}"],
                    "total_lines": 0,
                    "file_size": 0,
                    "error": str(e)
                }
        else:
            logs[log_file] = {
                "lines": ["日志文件不存在"],
                "total_lines": 0,
                "file_size": 0
            }
    
    return {
        "success": True, 
        "zbproxy_status": status_info,
        "logs": logs,
        "timestamp": time.time()
    }

@app.post("/logs/clear")
async def clear_logs():
    """清理日志文件"""
    cleared_files = []
    errors = []
    
    log_files = ["out.log", "zbproxy.log", "error.log", "access.log"]
    
    for log_file in log_files:
        if os.path.exists(log_file):
            try:
                # 清空文件内容而不是删除文件
                with open(log_file, 'w', encoding='utf-8') as f:
                    f.write("")
                cleared_files.append(log_file)
            except Exception as e:
                errors.append(f"{log_file}: {str(e)}")
    
    if cleared_files:
        message = f"已清理日志文件: {', '.join(cleared_files)}"
        if errors:
            message += f"，但以下文件清理失败: {', '.join(errors)}"
        return {"success": True, "message": message, "cleared_files": cleared_files}
    else:
        return {"success": False, "message": "没有找到可清理的日志文件", "errors": errors}

@app.get("/logs/tail/{filename}")
async def tail_log(filename: str, lines: int = 50):
    """获取指定日志文件的最后N行"""
    allowed_files = ["out.log", "zbproxy.log", "error.log", "access.log"]
    
    if filename not in allowed_files:
        raise HTTPException(status_code=400, detail=f"不允许访问的文件，仅支持: {', '.join(allowed_files)}")
    
    if not os.path.exists(filename):
        raise HTTPException(status_code=404, detail=f"日志文件不存在: {filename}")
    
    try:
        with open(filename, 'r', encoding='utf-8', errors='ignore') as f:
            all_lines = f.readlines()
            tail_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
            
        return {
            "success": True,
            "filename": filename,
            "total_lines": len(all_lines),
            "returned_lines": len(tail_lines),
            "lines": tail_lines,
            "file_size": os.path.getsize(filename)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取日志文件失败: {str(e)}")

@app.post("/dolinuxip")
async def do_linux_ip():
    """执行Linux IP路由设置脚本"""
    
    # 检查操作系统
    if os.name == 'nt':
        raise HTTPException(
            status_code=400, 
            detail="此功能仅支持Linux系统，当前系统为Windows"
        )
    
    # Linux脚本内容
    script_content = '''#!/bin/bash
# 自动获取 Anchor IP 并替换默认路由

# 公网接口名（通常是 eth0，先确认 ip addr 输出）
IFACE="eth0"

# 获取 Anchor IP
ANCHOR_IP=$(curl -s http://169.254.169.254/metadata/v1/interfaces/public/0/anchor_ipv4/gateway)

if [[ -n "$ANCHOR_IP" ]]; then
    echo "设置默认路由为 $ANCHOR_IP ($IFACE)"
    ip route replace default via $ANCHOR_IP dev $IFACE
else
    echo "未能获取 Anchor IP，退出。"
    exit 1
fi
'''
    
    try:
        # 创建临时脚本文件
        script_file = "/tmp/dolinuxip.sh"
        
        with open(script_file, 'w', encoding='utf-8') as f:
            f.write(script_content)
        
        # 给脚本文件执行权限
        os.chmod(script_file, 0o755)
        
        # 执行脚本
        result = subprocess.run(
            ['/bin/bash', script_file],
            capture_output=True,
            text=True,
            timeout=30  # 30秒超时
        )
        
        # 删除临时文件
        try:
            os.remove(script_file)
        except:
            pass
        
        if result.returncode == 0:
            return {
                "success": True,
                "message": "Linux IP路由设置成功",
                "output": result.stdout.strip(),
                "error": result.stderr.strip() if result.stderr else None
            }
        else:
            return {
                "success": False,
                "message": "Linux IP路由设置失败",
                "output": result.stdout.strip() if result.stdout else None,
                "error": result.stderr.strip(),
                "exit_code": result.returncode
            }
            
    except subprocess.TimeoutExpired:
        # 清理临时文件
        try:
            os.remove(script_file)
        except:
            pass
        raise HTTPException(status_code=408, detail="脚本执行超时（30秒）")
        
    except PermissionError:
        raise HTTPException(
            status_code=403, 
            detail="权限不足，需要root权限来修改网络路由"
        )
        
    except Exception as e:
        # 清理临时文件
        try:
            os.remove(script_file)
        except:
            pass
        raise HTTPException(
            status_code=500, 
            detail=f"执行脚本时发生错误: {str(e)}"
        )

if __name__ == "__main__":
    print("启动ZBProxy管理API...")
    print(f"配置文件: {CONFIG_FILE}")
    print(f"可执行文件: {ZBPROXY_EXECUTABLE}")
    print("API文档: http://localhost:8000/docs")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
