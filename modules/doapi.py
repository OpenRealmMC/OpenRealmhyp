import aiohttp
import requests
import json
import os
import asyncio
import logging
import time
import urllib3
from concurrent.futures import ThreadPoolExecutor

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 配置日志记录器
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# 缓存机制
_cache = {}
_cache_timeout = 30  # 缓存30秒

def _get_cache_key(token, endpoint):
    """生成缓存键"""
    return f"{token[:10]}_{endpoint}"

def _get_from_cache(cache_key):
    """从缓存获取数据"""
    if cache_key in _cache:
        data, timestamp = _cache[cache_key]
        if time.time() - timestamp < _cache_timeout:
            return data
        else:
            # 清除过期缓存
            del _cache[cache_key]
    return None

def _set_cache(cache_key, data):
    """设置缓存"""
    _cache[cache_key] = (data, time.time())

def clear_cache():
    """清除所有缓存"""
    global _cache
    _cache = {}


def get_config():
    """
    从 config.json 文件中获取完整配置
    
    返回:
        dict: 配置字典
    
    异常:
        FileNotFoundError: 配置文件不存在
        json.JSONDecodeError: JSON 格式错误
    """
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'config.json')
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"Invalid JSON in config file: {e}")


def get_do_token(token_key):
    """
    从 config.json 文件中获取 DigitalOcean API token
    
    参数:
        token_key (str or int): token 键名（如 'do_token1'）或编号（如 1）
    
    返回:
        str: DigitalOcean API token
    
    异常:
        FileNotFoundError: 配置文件不存在
        KeyError: 指定的 token 不存在
        json.JSONDecodeError: JSON 格式错误
    """
    config = get_config()
    
    # 支持传入数字或字符串
    if isinstance(token_key, int):
        token_key = f"do_token{token_key}"
    
    if token_key not in config:
        raise KeyError(f"Token '{token_key}' not found in config.json")
    
    token = config[token_key]
    
    # 如果 token 包含前缀说明，提取实际的 token
    if ":" in token:
        token = token.split(":", 1)[1].strip()
    
    return token


def get_machine_token(machine_name):
    """
    根据机器名称获取对应的 DigitalOcean API token
    
    参数:
        machine_name (str): 机器名称
    
    返回:
        str: DigitalOcean API token
    
    异常:
        KeyError: 机器不存在或 token 配置错误
    """
    config = get_config()
    
    machines = config.get('machines', {})
    if machine_name not in machines:
        raise KeyError(f"Machine '{machine_name}' not found in config")
    
    machine_config = machines[machine_name]
    token_key = machine_config.get('usedo', 'do_token1')  # 默认使用 do_token1
    
    return get_do_token(token_key)


def get_machine_info(machine_name):
    """
    根据机器名称获取机器信息和对应的 token
    
    参数:
        machine_name (str): 机器名称
    
    返回:
        tuple: (machine_id, token, machine_config)
    
    异常:
        KeyError: 机器不存在
    """
    config = get_config()
    
    machines = config.get('machines', {})
    if machine_name not in machines:
        available_machines = list(machines.keys())
        raise KeyError(f"Machine '{machine_name}' not found. Available machines: {available_machines}")
    
    machine_config = machines[machine_name]
    machine_id = machine_config.get('id')
    token = get_machine_token(machine_name)
    
    return machine_id, token, machine_config


async def get_all_tokens_reserved_ips(use_cache=True):
    """
    并行获取所有 token 的 Reserved IP（高性能版本）
    
    参数:
        use_cache (bool): 是否使用缓存
    
    返回:
        dict: {token_key: {'reserved_ips': [...], 'status': 'success'/'failed', 'error': '...'}}
    """
    config = get_config()
    tokens = {key: value for key, value in config.items() if key.startswith('do_token')}
    
    if not tokens:
        return {}
    
    async def fetch_token_data(token_key, token_value):
        """获取单个 token 的数据"""
        try:
            # 提取实际的 token
            if ":" in token_value:
                token = token_value.split(":", 1)[1].strip()
            else:
                token = token_value
            
            # 并行获取 Reserved IP
            reserved_ips = await get_reserved_ips(token, use_cache=use_cache)
            
            return {
                'token_key': token_key,
                'reserved_ips': reserved_ips,
                'reserved_ips_count': len(reserved_ips),
                'status': 'success'
            }
        except Exception as e:
            logger.error(f"Failed to fetch data for {token_key}: {e}")
            return {
                'token_key': token_key,
                'reserved_ips': [],
                'reserved_ips_count': 0,
                'status': 'failed',
                'error': str(e)
            }
    
    # 并行执行所有 token 的查询
    start_time = time.time()
    tasks = [fetch_token_data(token_key, token_value) for token_key, token_value in tokens.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    execution_time = time.time() - start_time
    logger.info(f"Parallel fetch completed in {execution_time:.2f} seconds for {len(tokens)} accounts")
    
    # 整理结果
    all_results = {}
    for result in results:
        if isinstance(result, dict):
            token_key = result['token_key']
            all_results[token_key] = result
        else:
            logger.error(f"Unexpected result type: {type(result)}")
    
    return all_results


async def get_droplets_cached(token, use_cache=True):
    """
    获取 Droplets 列表（带缓存）
    
    参数:
        token (str): DigitalOcean API 访问令牌
        use_cache (bool): 是否使用缓存
    
    返回:
        dict: Droplets 字典
    """
    # 检查缓存
    if use_cache:
        cache_key = _get_cache_key(token, "droplets")
        cached_data = _get_from_cache(cache_key)
        if cached_data is not None:
            logger.debug(f"Using cached Droplets data ({len(cached_data)} items)")
            return cached_data
    
    # 获取新数据
    droplets = await get_droplets(token)
    
    # 设置缓存
    if use_cache and droplets:
        cache_key = _get_cache_key(token, "droplets")
        _set_cache(cache_key, droplets)
    
    return droplets


async def extract_droplet_details(data):
    """提取每台 Droplet 的 ID 和对应的 V4 公网 IP"""
    try:
        droplets = data.get("droplets", [])
        result = {}
        for droplet in droplets:
            droplet_id = droplet.get("id")
            name = droplet.get("name")
            v4_ips = [
                ip_info["ip_address"]
                for ip_info in droplet.get("networks", {}).get("v4", [])
                if ip_info.get("type") == "public"
            ]
            if droplet_id and name and v4_ips:  # 只添加有效的数据
                result[name] = {"id": droplet_id, "v4_ips": v4_ips}
        return result
    except Exception as e:
        logger.error(f"Error extracting droplet details: {str(e)}")
        return {}


async def get_droplets(token, retries=3, proxy=None):
    """获取所有 Droplets 的详情"""
    url = "https://api.digitalocean.com/v2/droplets"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    for attempt in range(retries):
        try:
            response = requests.get(
                url, 
                headers=headers,
                timeout=10,
                verify=False,  # 直连不需要代理和验证
                proxies=proxy if proxy else None
            )
            
            if response.status_code == 200:
                data = response.json()
                result = await extract_droplet_details(data)
                if result:
                    return result
                logger.warning(f"Empty droplet list received on attempt {attempt+1}")
            else:
                logger.warning(f"Failed to get droplets. Status: {response.status_code}")
                
            if attempt < retries - 1:
                await asyncio.sleep(2)
                
        except Exception as e:
            logger.error(f"Request failed on attempt {attempt+1}: {str(e)}")
            if attempt < retries - 1:
                await asyncio.sleep(2)
    
    return {}

async def get_new_ip_for_droplet(token, droplet_id, retries=3):
    """获取 Droplet 的新 IP 地址"""
    url = f"https://api.digitalocean.com/v2/droplets/{droplet_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    for attempt in range(retries):
        try:
            response = requests.get(
                url, 
                headers=headers,
                timeout=10,
                verify=False
            )
            if response.status_code == 200:
                droplet_info = response.json()
                new_ip = droplet_info['droplet']['networks']['v4'][0]['ip_address']
                return new_ip
            else:
                logger.warning(f"Failed to get IP for Droplet {droplet_id}. Status code: {response.status_code}")
                return None
        except Exception as e:
            logger.warning(f"Failed to get IP for Droplet {droplet_id}. Attempt {attempt + 1}/{retries}.")
            if attempt < retries - 1:
                await asyncio.sleep(2)
    return False

async def find_do_server(identifier, token):
    """
    根据 IP 或 Droplet ID 查找机器名称和 ID

    参数:
        identifier (str): 机器的 IP 地址或 Droplet ID
        token (str): DigitalOcean API 的访问令牌

    返回:
        tuple: 如果找到对应的机器，返回机器名称和 ID；否则返回 None
    """
    All = await get_droplets(token)

    if All is None:
        logger.warning("Could not retrieve Droplet information.")
        return None

    for machine_name, machine_info in All.items():
        if identifier in machine_info.get("v4_ips", []):
            return machine_name, machine_info.get("id")
        if str(identifier) == str(machine_info.get("id", None)):
            return machine_name, machine_info.get("id")
    return None


async def create_reserved_ip(token, droplet_id, retries=3):
    """
    为指定的 Droplet 创建 Reserved IP
    
    参数:
        token (str): DigitalOcean API 访问令牌
        droplet_id (int): Droplet 的 ID
        retries (int): 重试次数
    
    返回:
        dict: 包含 Reserved IP 信息的字典，失败时返回 None
    """
    url = "https://api.digitalocean.com/v2/reserved_ips"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    data = {"droplet_id": droplet_id}
    
    for attempt in range(retries):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=10,
                verify=False
            )
            
            if response.status_code in [201, 202]:  # 201 Created, 202 Accepted
                result = response.json()
                reserved_ip_info = result.get('reserved_ip', {})
                ip_address = reserved_ip_info.get('ip')
                logger.info(f"Successfully created Reserved IP: {ip_address}")
                return reserved_ip_info
            elif response.status_code == 422:
                # 处理配额限制错误
                try:
                    error_data = response.json()
                    error_message = error_data.get('message', 'Unknown error')
                    if 'exceed your Reserved IP limit' in error_message:
                        logger.error(f"Reserved IP limit exceeded: {error_message}")
                        return {"error": "quota_exceeded", "message": error_message}
                    else:
                        logger.error(f"Unprocessable entity: {error_message}")
                        return {"error": "unprocessable_entity", "message": error_message}
                except:
                    logger.error(f"Failed to create Reserved IP. Status: {response.status_code}, Response: {response.text}")
                    return {"error": "unprocessable_entity", "status_code": response.status_code}
            else:
                logger.warning(f"Failed to create Reserved IP. Status: {response.status_code}, Response: {response.text}")
                
            if attempt < retries - 1:
                await asyncio.sleep(2)
                
        except Exception as e:
            logger.error(f"Request failed on attempt {attempt+1}: {str(e)}")
            if attempt < retries - 1:
                await asyncio.sleep(2)
    
    return None


async def get_reserved_ips(token, retries=3, use_cache=True):
    """
    获取所有 Reserved IP 列表（带缓存）
    
    参数:
        token (str): DigitalOcean API 访问令牌
        retries (int): 重试次数
        use_cache (bool): 是否使用缓存
    
    返回:
        list: Reserved IP 列表，失败时返回空列表
    """
    # 检查缓存
    if use_cache:
        cache_key = _get_cache_key(token, "reserved_ips")
        cached_data = _get_from_cache(cache_key)
        if cached_data is not None:
            logger.debug(f"Using cached Reserved IPs data ({len(cached_data)} items)")
            return cached_data
    
    url = "https://api.digitalocean.com/v2/reserved_ips"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    for attempt in range(retries):
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=10,
                verify=False
            )
            
            if response.status_code == 200:
                data = response.json()
                reserved_ips = data.get('reserved_ips', [])
                logger.info(f"Successfully retrieved {len(reserved_ips)} Reserved IPs")
                
                # 设置缓存
                if use_cache:
                    cache_key = _get_cache_key(token, "reserved_ips")
                    _set_cache(cache_key, reserved_ips)
                
                return reserved_ips
            else:
                logger.warning(f"Failed to get Reserved IPs. Status: {response.status_code}")
                
            if attempt < retries - 1:
                await asyncio.sleep(2)
                
        except Exception as e:
            logger.error(f"Request failed on attempt {attempt+1}: {str(e)}")
            if attempt < retries - 1:
                await asyncio.sleep(2)
    
    return []


async def manage_reserved_ip_assignment(token, reserved_ip, action, droplet_id=None, retries=3):
    """
    管理 Reserved IP 的分配状态（分配、解除分配或重新分配）
    
    参数:
        token (str): DigitalOcean API 访问令牌
        reserved_ip (str): Reserved IP 地址
        action (str): 操作类型 ('assign', 'unassign')
        droplet_id (int, optional): 当 action 为 'assign' 时需要提供 Droplet ID
        retries (int): 重试次数
    
    返回:
        dict: 操作结果信息，失败时返回 None
    """
    url = f"https://api.digitalocean.com/v2/reserved_ips/{reserved_ip}/actions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    # 构建请求数据
    data = {"type": action}
    if action == "assign" and droplet_id:
        data["resource_id"] = droplet_id
    
    for attempt in range(retries):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=10,
                verify=False
            )
            
            if response.status_code == 201:
                result = response.json()
                action_info = result.get('action', {})
                logger.info(f"Successfully {action}ed Reserved IP {reserved_ip}")
                return action_info
            else:
                logger.warning(f"Failed to {action} Reserved IP {reserved_ip}. Status: {response.status_code}, Response: {response.text}")
                
            if attempt < retries - 1:
                await asyncio.sleep(2)
                
        except Exception as e:
            logger.error(f"Request failed on attempt {attempt+1}: {str(e)}")
            if attempt < retries - 1:
                await asyncio.sleep(2)
    
    return None


async def delete_reserved_ip(token, reserved_ip, retries=3):
    """
    删除指定的 Reserved IP（优化版本，处理pending event错误）
    
    参数:
        token (str): DigitalOcean API 访问令牌
        reserved_ip (str): 要删除的 Reserved IP 地址
        retries (int): 重试次数
    
    返回:
        bool: 删除成功返回 True，失败返回 False 或错误字典
    """
    url = f"https://api.digitalocean.com/v2/reserved_ips/{reserved_ip}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    for attempt in range(retries):
        try:
            response = requests.delete(
                url,
                headers=headers,
                timeout=10,
                verify=False
            )
            
            if response.status_code == 204:
                logger.info(f"Successfully deleted Reserved IP: {reserved_ip}")
                return True
            elif response.status_code == 404:
                # 处理404错误 - Reserved IP不存在
                try:
                    error_data = response.json()
                    error_message = error_data.get('message', 'Reserved IP not found')
                    logger.error(f"Reserved IP {reserved_ip} not found: {error_message}")
                    return {"error": "not_found", "message": error_message, "ip": reserved_ip}
                except:
                    logger.error(f"Reserved IP {reserved_ip} not found (404)")
                    return {"error": "not_found", "message": "Reserved IP not found or already deleted", "ip": reserved_ip}
            elif response.status_code == 422:
                # 处理422错误 - pending event或其他不可处理的实体错误
                try:
                    error_data = response.json()
                    error_message = error_data.get('message', 'Unprocessable entity')
                    error_id = error_data.get('id', 'unknown')
                    
                    if 'pending event' in error_message.lower():
                        logger.warning(f"Reserved IP {reserved_ip} has pending event, waiting before retry...")
                        if attempt < retries - 1:
                            # 对于pending event，等待更长时间
                            await asyncio.sleep(5 + attempt * 2)  # 5s, 7s, 9s
                            continue
                        else:
                            logger.error(f"Reserved IP {reserved_ip} still has pending event after {retries} attempts")
                            return {"error": "pending_event", "message": error_message, "ip": reserved_ip}
                    else:
                        logger.error(f"Unprocessable entity for Reserved IP {reserved_ip}: {error_message}")
                        return {"error": "unprocessable_entity", "message": error_message, "ip": reserved_ip}
                except:
                    logger.error(f"Failed to delete Reserved IP {reserved_ip}. Status: 422, Response: {response.text}")
                    return {"error": "unprocessable_entity", "message": "Unknown 422 error", "ip": reserved_ip}
            else:
                logger.warning(f"Failed to delete Reserved IP {reserved_ip}. Status: {response.status_code}, Response: {response.text}")
                
            if attempt < retries - 1:
                await asyncio.sleep(3 + attempt)  # 递增等待时间: 3s, 4s, 5s
                
        except Exception as e:
            logger.error(f"Request failed on attempt {attempt+1}: {str(e)}")
            if attempt < retries - 1:
                await asyncio.sleep(3 + attempt)
    
    return False


async def assign_reserved_ip_to_droplet(token, reserved_ip, droplet_id, retries=3):
    """
    将 Reserved IP 分配给指定的 Droplet
    
    参数:
        token (str): DigitalOcean API 访问令牌
        reserved_ip (str): Reserved IP 地址
        droplet_id (int): 目标 Droplet 的 ID
        retries (int): 重试次数
    
    返回:
        dict: 操作结果，失败时返回 None
    """
    return await manage_reserved_ip_assignment(token, reserved_ip, "assign", droplet_id, retries)


async def unassign_reserved_ip(token, reserved_ip, retries=3):
    """
    解除 Reserved IP 的分配
    
    参数:
        token (str): DigitalOcean API 访问令牌
        reserved_ip (str): Reserved IP 地址
        retries (int): 重试次数
    
    返回:
        dict: 操作结果，失败时返回 None
    """
    return await manage_reserved_ip_assignment(token, reserved_ip, "unassign", None, retries)



