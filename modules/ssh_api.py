import asyncssh
from modules import Api
import time

private_key_path = Api.get_ssh_key_path()
private_key_password = Api.get_ssh_key_password()

async def ssh_execute_with_password(ip, password, command, port=22, retries=3):
    """异步通过 SSH 使用密码认证执行命令"""
    for attempt in range(retries):
        try:
            Api.logger.info(f"正在尝试连接后端服务器 {ip}，第 {attempt + 1} 次尝试...")
            Api.logger.info(f"使用用户名: root, 端口: {port}")
            
            # 添加更多连接选项
            async with asyncssh.connect(
                ip, 
                port=port, 
                username="root",
                password=password,
                known_hosts=None,
                client_keys=None,
                preferred_auth="password",
                connect_timeout=30,
                login_timeout=30
            ) as conn:
                Api.logger.info(f"SSH 连接成功，正在执行命令: {command}")
                result = await conn.run(command, check=True)
                Api.logger.success(f"命令执行成功: {result.stdout}")
                return result.stdout

        except asyncssh.PermissionDenied as e:
            Api.logger.error(f"SSH 权限被拒绝: {str(e)}")
            Api.logger.info("请检查用户名和密码是否正确")
            if attempt < retries - 1:
                Api.logger.info(f"等待 3 秒后重试...")
                time.sleep(3)
            continue
            
        except asyncssh.ConnectionFailed as e:
            Api.logger.error(f"SSH 连接失败: {str(e)}")
            Api.logger.info("请检查网络连接和防火墙设置")
            if attempt < retries - 1:
                Api.logger.info(f"等待 3 秒后重试...")
                time.sleep(3)
            continue
            
        except asyncssh.Error as e:
            Api.logger.error(f"SSH 错误: {str(e)}")
            Api.logger.info(f"详细错误信息: {e.__class__.__name__}")
            if attempt < retries - 1:
                Api.logger.info(f"等待 3 秒后重试...")
                time.sleep(3)
            continue
            
        except Exception as e:
            Api.logger.error(f"未知错误: {str(e)}")
            Api.logger.info(f"错误类型: {type(e)}")
            if attempt < retries - 1:
                Api.logger.info(f"等待 3 秒后重试...")
                time.sleep(3)
            continue

    Api.logger.error(f"在 {retries} 次尝试后仍然无法连接到服务器 {ip}")
    return False

async def ssh_execute(ip, command, port=22, retries=3):
    """异步通过 SSH 密钥执行命令"""
    global private_key_path, private_key_password
    
    for attempt in range(retries):
        try:
            Api.logger.info(f"正在尝试使用密钥连接服务器 {ip}，第 {attempt + 1} 次尝试...")
            
            async with asyncssh.connect(
                ip, 
                port=port, 
                username="root",
                client_keys=[private_key_path],
                passphrase=private_key_password,
                known_hosts=None,
                connect_timeout=30
            ) as conn:
                Api.logger.info(f"SSH 连接成功，正在执行命令: {command}")
                result = await conn.run(command, check=True)
                Api.logger.success(f"命令执行成功")
                return result.stdout

        except Exception as e:
            Api.logger.error(f"SSH 连接失败（第 {attempt + 1} 次）：{e}")
            if attempt < retries - 1:
                Api.logger.info(f"等待 3 秒后重试...")
                time.sleep(3)
            continue

    Api.logger.error(f"在 {retries} 次尝试后仍然无法连接到服务器 {ip}")
    return False
