#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reserved IP 管理 Flask API
用于管理指定机器的 DigitalOcean Reserved IP

支持的机器：
- hyp-openrealm-us1 (ID: 518135617)
- hyp-openrealm-us (ID: 518150357)
- hyp-openrealm-us2 (ID: 518150443)
"""

import asyncio
import json
import os
from flask import Flask, request, jsonify
from functools import wraps
from modules import doapi

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 支持中文字符


def get_config():
    """获取配置信息"""
    config_path = os.path.join('config', 'config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        raise Exception(f"无法读取配置文件: {e}")


def async_route(f):
    """装饰器：使 Flask 路由支持异步函数"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(f(*args, **kwargs))
        finally:
            loop.close()
    return wrapper


def get_machine_details(machine_name):
    """根据机器名称获取机器详情和对应的 token"""
    try:
        machine_id, token, machine_config = doapi.get_machine_info(machine_name)
        return {
            'id': machine_id,
            'token': token,
            'config': machine_config,
            'token_key': machine_config.get('usedo', 'do_token1')
        }
    except Exception as e:
        raise Exception(f"获取机器信息失败: {e}")


def get_machine_id(machine_name):
    """根据机器名称获取 Droplet ID（保持向后兼容）"""
    machine_details = get_machine_details(machine_name)
    return machine_details['id']


def success_response(data=None, message="操作成功"):
    """成功响应格式"""
    response = {
        "success": True,
        "message": message,
        "data": data
    }
    return jsonify(response)


def error_response(message, code=400, details=None):
    """错误响应格式"""
    response = {
        "success": False,
        "message": message,
        "error_code": code
    }
    if details:
        response["details"] = details
    return jsonify(response), code


@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查端点"""
    return success_response({"status": "running"}, "API 服务正常运行")


@app.route('/api/machines', methods=['GET'])
def get_machines():
    """获取所有可用的机器列表"""
    try:
        config = doapi.get_config()
        machines = config.get('machines', {})
        
        # 添加每个机器使用的token信息
        enhanced_machines = {}
        for machine_name, machine_config in machines.items():
            enhanced_config = machine_config.copy()
            token_key = machine_config.get('usedo', 'do_token1')
            enhanced_config['token_key'] = token_key
            enhanced_config['token_available'] = token_key in config
            enhanced_machines[machine_name] = enhanced_config
        
        return success_response(enhanced_machines, "获取机器列表成功")
    except Exception as e:
        return error_response(f"获取机器列表失败: {str(e)}")


def simplify_reserved_ip_data(reserved_ips):
    """
    简化 Reserved IP 数据，只保留关键信息
    
    参数:
        reserved_ips (list): 原始的 Reserved IP 列表
    
    返回:
        list: 简化后的 Reserved IP 列表
    """
    simplified = []
    
    for rip in reserved_ips:
        droplet = rip.get('droplet')
        simplified_rip = {
            'ip': rip.get('ip'),
            'region': {
                'name': rip.get('region', {}).get('name'),
                'slug': rip.get('region', {}).get('slug')
            },
            'locked': rip.get('locked', False),
            'project_id': rip.get('project_id')
        }
        
        if droplet:
            simplified_rip['droplet'] = {
                'id': droplet.get('id'),
                'name': droplet.get('name'),
                'status': droplet.get('status'),
                'created_at': droplet.get('created_at'),
                'memory': droplet.get('memory'),
                'vcpus': droplet.get('vcpus'),
                'disk': droplet.get('disk'),
                'current_ip': None  # 将在后面填充
            }
            
            # 获取当前的公网IP
            networks = droplet.get('networks', {}).get('v4', [])
            for network in networks:
                if network.get('type') == 'public':
                    simplified_rip['droplet']['current_ip'] = network.get('ip_address')
                    break
        else:
            simplified_rip['droplet'] = None
            
        simplified.append(simplified_rip)
    
    return simplified


@app.route('/api/reserved-ips', methods=['GET'])
@async_route
async def get_all_reserved_ips():
    """获取所有 Reserved IP（默认获取所有账户）"""
    try:
        # 检查是否指定了特定的 token
        token_param = request.args.get('token')
        all_accounts = request.args.get('all_accounts', 'true').lower() == 'true'
        
        if token_param and not all_accounts:
            # 单个 token 查询（向后兼容）
            token = doapi.get_do_token(token_param)
            reserved_ips = await doapi.get_reserved_ips(token)
            
            # 检查是否需要简化数据
            simplify = request.args.get('simplify', 'false').lower() == 'true'
            
            if simplify:
                simplified_data = simplify_reserved_ip_data(reserved_ips)
                return success_response(simplified_data, f"获取到 {len(simplified_data)} 个 Reserved IP（简化格式，{token_param}）")
            else:
                return success_response(reserved_ips, f"获取到 {len(reserved_ips)} 个 Reserved IP（完整格式，{token_param}）")
        
        else:
            # 默认获取所有账户的 Reserved IP（新行为）
            use_cache = request.args.get('no_cache', 'false').lower() != 'true'
            all_results = await doapi.get_all_tokens_reserved_ips(use_cache=use_cache)
            
            if not all_results:
                return success_response([], "没有找到任何配置的 token")
            
            # 合并所有账户的 Reserved IP
            all_reserved_ips = []
            successful_accounts = 0
            
            for token_key, result in all_results.items():
                if result.get('status') == 'success':
                    successful_accounts += 1
                    reserved_ips = result.get('reserved_ips', [])
                    
                    # 为每个 Reserved IP 添加来源账户信息
                    for rip in reserved_ips:
                        rip['_source_account'] = token_key
                    
                    all_reserved_ips.extend(reserved_ips)
            
            # 检查是否需要简化数据
            simplify = request.args.get('simplify', 'false').lower() == 'true'
            
            if simplify:
                simplified_data = simplify_reserved_ip_data(all_reserved_ips)
                return success_response(
                    simplified_data, 
                    f"获取到 {len(simplified_data)} 个 Reserved IP（简化格式，来自 {successful_accounts} 个账户）"
                )
            else:
                return success_response(
                    all_reserved_ips, 
                    f"获取到 {len(all_reserved_ips)} 个 Reserved IP（完整格式，来自 {successful_accounts} 个账户）"
                )
            
    except Exception as e:
        return error_response(f"获取 Reserved IP 列表失败: {str(e)}")


@app.route('/api/reserved-ips/summary', methods=['GET'])
@async_route
async def get_reserved_ips_summary():
    """获取 Reserved IP 摘要信息"""
    try:
        token = doapi.get_do_token(1)
        reserved_ips = await doapi.get_reserved_ips(token)
        
        summary = []
        for rip in reserved_ips:
            droplet = rip.get('droplet')
            
            summary_item = {
                'reserved_ip': rip.get('ip'),
                'region': rip.get('region', {}).get('name', 'N/A'),
                'status': 'assigned' if droplet else 'unassigned'
            }
            
            if droplet:
                summary_item.update({
                    'machine_name': droplet.get('name'),
                    'machine_id': droplet.get('id'),
                    'machine_status': droplet.get('status')
                })
            
            summary.append(summary_item)
        
        return success_response(summary, f"获取到 {len(summary)} 个 Reserved IP 摘要")
        
    except Exception as e:
        return error_response(f"获取 Reserved IP 摘要失败: {str(e)}")


@app.route('/api/reserved-ips/quota', methods=['GET'])
@async_route
async def get_reserved_ip_quota():
    """获取 Reserved IP 配额信息"""
    try:
        token = doapi.get_do_token(1)
        reserved_ips = await doapi.get_reserved_ips(token)
        
        current_count = len(reserved_ips)
        assigned_count = sum(1 for rip in reserved_ips if rip.get('droplet'))
        unassigned_count = current_count - assigned_count
        
        # DigitalOcean 默认的 Reserved IP 限制通常是 3 个
        # 这个数值可能因账户类型而异，但我们可以通过尝试创建来检测
        estimated_limit = 3  # 这是一个估算值
        
        quota_info = {
            'current_count': current_count,
            'assigned_count': assigned_count,
            'unassigned_count': unassigned_count,
            'estimated_limit': estimated_limit,
            'can_create_more': current_count < estimated_limit,
            'usage_percentage': round((current_count / estimated_limit) * 100, 1) if estimated_limit > 0 else 0,
            'details': {
                'assigned_ips': [
                    {
                        'ip': rip.get('ip'),
                        'machine_name': rip.get('droplet', {}).get('name'),
                        'machine_id': rip.get('droplet', {}).get('id')
                    }
                    for rip in reserved_ips if rip.get('droplet')
                ],
                'unassigned_ips': [
                    {
                        'ip': rip.get('ip'),
                        'region': rip.get('region', {}).get('name')
                    }
                    for rip in reserved_ips if not rip.get('droplet')
                ]
            }
        }
        
        if current_count >= estimated_limit:
            message = f"已达到 Reserved IP 限制 ({current_count}/{estimated_limit})，无法创建更多"
        else:
            remaining = estimated_limit - current_count
            message = f"当前使用 {current_count}/{estimated_limit} 个 Reserved IP，还可创建 {remaining} 个"
        
        return success_response(quota_info, message)
        
    except Exception as e:
        return error_response(f"获取 Reserved IP 配额信息失败: {str(e)}")


@app.route('/api/reserved-ips/all-accounts', methods=['GET'])
@async_route
async def get_all_accounts_reserved_ips():
    """获取所有账户的 Reserved IP（高性能并行版本）"""
    try:
        import time
        start_time = time.time()
        
        # 使用并行查询获取所有 token 的数据
        use_cache = request.args.get('no_cache', 'false').lower() != 'true'
        all_results = await doapi.get_all_tokens_reserved_ips(use_cache=use_cache)
        
        if not all_results:
            return success_response(
                {'accounts': {}, 'summary': {'total_accounts': 0, 'successful_accounts': 0, 'total_reserved_ips': 0}},
                "没有找到任何配置的 token"
            )
        
        # 获取机器配置信息
        config = doapi.get_config()
        machines = config.get('machines', {})
        
        # 为每个账户添加使用该 token 的机器信息
        for token_key, result in all_results.items():
            if result.get('status') == 'success':
                using_machines = []
                for machine_name, machine_config in machines.items():
                    if machine_config.get('usedo', 'do_token1') == token_key:
                        using_machines.append({
                            'name': machine_name,
                            'id': machine_config.get('id'),
                            'description': machine_config.get('description', '')
                        })
                result['using_machines'] = using_machines
        
        # 计算总计
        total_reserved_ips = sum(
            result.get('reserved_ips_count', 0) 
            for result in all_results.values() 
            if result.get('status') == 'success'
        )
        
        successful_accounts = sum(1 for r in all_results.values() if r.get('status') == 'success')
        execution_time = time.time() - start_time
        
        return success_response(
            {
                'accounts': all_results,
                'summary': {
                    'total_accounts': len(all_results),
                    'successful_accounts': successful_accounts,
                    'total_reserved_ips': total_reserved_ips,
                    'execution_time': round(execution_time, 2),
                    'cache_used': use_cache
                }
            },
            f"⚡ 快速获取到 {len(all_results)} 个账户的信息，共 {total_reserved_ips} 个 Reserved IP（耗时 {execution_time:.2f}s）"
        )
        
    except Exception as e:
        return error_response(f"获取所有账户 Reserved IP 失败: {str(e)}")


@app.route('/api/machines/<machine_name>/reserved-ip', methods=['POST'])
@async_route
async def create_reserved_ip(machine_name):
    """为指定机器创建 Reserved IP"""
    try:
        # 获取机器详情和对应的token
        machine_details = get_machine_details(machine_name)
        droplet_id = machine_details['id']
        token = machine_details['token']
        
        print(f"🎯 为机器 {machine_name} (ID: {droplet_id}) 创建 Reserved IP，使用 token: {machine_details['token_key']}")
        
        # 创建Reserved IP
        reserved_ip_info = await doapi.create_reserved_ip(token, droplet_id)
        
        if reserved_ip_info:
            # 检查是否是配额限制错误
            if isinstance(reserved_ip_info, dict) and reserved_ip_info.get('error') == 'quota_exceeded':
                return error_response(
                    f"无法为机器 {machine_name} 创建 Reserved IP：已达到账户限制",
                    400,
                    {
                        "error_type": "quota_exceeded",
                        "suggestion": "请先删除其他不用的 Reserved IP，或联系 DigitalOcean 提升配额限制",
                        "original_message": reserved_ip_info.get('message')
                    }
                )
            
            # 检查其他错误
            if isinstance(reserved_ip_info, dict) and 'error' in reserved_ip_info:
                return error_response(
                    f"为机器 {machine_name} 创建 Reserved IP 失败：{reserved_ip_info.get('message', '未知错误')}",
                    400,
                    reserved_ip_info
                )
            
            return success_response(
                reserved_ip_info, 
                f"成功为机器 {machine_name} 创建 Reserved IP: {reserved_ip_info.get('ip')}"
            )
        else:
            return error_response(f"为机器 {machine_name} 创建 Reserved IP 失败")
            
    except Exception as e:
        return error_response(f"创建 Reserved IP 失败: {str(e)}")


@app.route('/api/reserved-ips/<reserved_ip>/assign', methods=['POST'])
@async_route
async def assign_reserved_ip(reserved_ip):
    """将 Reserved IP 分配给指定机器"""
    try:
        data = request.get_json() or {}
        machine_name = data.get('machine_name')
        
        if not machine_name:
            return error_response("请提供 machine_name 参数")
        
        # 获取机器ID
        droplet_id = get_machine_id(machine_name)
        
        # 分配Reserved IP
        token = doapi.get_do_token(1)
        result = await doapi.assign_reserved_ip_to_droplet(token, reserved_ip, droplet_id)
        
        if result:
            return success_response(
                result, 
                f"成功将 Reserved IP {reserved_ip} 分配给机器 {machine_name}"
            )
        else:
            return error_response(f"分配 Reserved IP {reserved_ip} 到机器 {machine_name} 失败")
            
    except Exception as e:
        return error_response(f"分配 Reserved IP 失败: {str(e)}")


@app.route('/api/reserved-ips/<reserved_ip>/unassign', methods=['POST'])
@async_route
async def unassign_reserved_ip(reserved_ip):
    """解除 Reserved IP 的分配"""
    try:
        token = doapi.get_do_token(1)
        result = await doapi.unassign_reserved_ip(token, reserved_ip)
        
        if result:
            return success_response(
                result, 
                f"成功解除 Reserved IP {reserved_ip} 的分配"
            )
        else:
            return error_response(f"解除 Reserved IP {reserved_ip} 分配失败")
            
    except Exception as e:
        return error_response(f"解除 Reserved IP 分配失败: {str(e)}")


@app.route('/api/reserved-ips/<reserved_ip>', methods=['DELETE'])
@async_route
async def delete_reserved_ip(reserved_ip):
    """删除 Reserved IP"""
    try:
        # 使用并行查询快速找到这个 Reserved IP 属于哪个账户
        all_results = await doapi.get_all_tokens_reserved_ips(use_cache=True)
        
        found_token = None
        found_token_key = None
        
        # 在所有结果中查找这个 Reserved IP
        for token_key, result in all_results.items():
            if result.get('status') == 'success':
                reserved_ips = result.get('reserved_ips', [])
                if any(rip.get('ip') == reserved_ip for rip in reserved_ips):
                    # 提取实际的 token
                    config = doapi.get_config()
                    token_value = config.get(token_key, '')
                    if ":" in token_value:
                        found_token = token_value.split(":", 1)[1].strip()
                    else:
                        found_token = token_value
                    found_token_key = token_key
                    break
        
        if not found_token:
            return error_response(
                f"Reserved IP {reserved_ip} 不存在或不属于任何已配置的账户",
                404,
                {
                    "error_type": "not_found",
                    "suggestion": "请检查 IP 地址是否正确，或该 IP 是否已被删除",
                    "searched_accounts": list(all_results.keys())
                }
            )
        
        print(f"🗑️ 删除 Reserved IP {reserved_ip}，使用 token: {found_token_key}")
        
        result = await doapi.delete_reserved_ip(found_token, reserved_ip)
        
        if result is True:
            return success_response(
                {
                    "deleted_ip": reserved_ip,
                    "used_token": found_token_key
                }, 
                f"成功删除 Reserved IP {reserved_ip}"
            )
        elif isinstance(result, dict) and result.get('error') == 'not_found':
            return error_response(
                f"Reserved IP {reserved_ip} 不存在或已被删除",
                404,
                {
                    "error_type": "not_found",
                    "original_message": result.get('message'),
                    "used_token": found_token_key
                }
            )
        else:
            return error_response(f"删除 Reserved IP {reserved_ip} 失败")
            
    except Exception as e:
        return error_response(f"删除 Reserved IP 失败: {str(e)}")


@app.route('/api/reserved-ips/<reserved_ip>/locate', methods=['GET'])
@async_route
async def locate_reserved_ip(reserved_ip):
    """查找 Reserved IP 属于哪个账户"""
    try:
        config = doapi.get_config()
        tokens = {key: value for key, value in config.items() if key.startswith('do_token')}
        
        for token_key, token_value in tokens.items():
            try:
                if ":" in token_value:
                    token = token_value.split(":", 1)[1].strip()
                else:
                    token = token_value
                
                reserved_ips = await doapi.get_reserved_ips(token)
                
                for rip in reserved_ips:
                    if rip.get('ip') == reserved_ip:
                        droplet = rip.get('droplet')
                        
                        # 找出使用该token的机器
                        using_machines = []
                        machines = config.get('machines', {})
                        for machine_name, machine_config in machines.items():
                            if machine_config.get('usedo', 'do_token1') == token_key:
                                using_machines.append({
                                    'name': machine_name,
                                    'id': machine_config.get('id'),
                                    'description': machine_config.get('description', '')
                                })
                        
                        result = {
                            'reserved_ip': reserved_ip,
                            'token_key': token_key,
                            'region': rip.get('region', {}).get('name', 'N/A'),
                            'status': 'assigned' if droplet else 'unassigned',
                            'using_machines': using_machines
                        }
                        
                        if droplet:
                            result['assigned_to'] = {
                                'machine_name': droplet.get('name'),
                                'machine_id': droplet.get('id'),
                                'machine_status': droplet.get('status')
                            }
                        
                        return success_response(
                            result,
                            f"找到 Reserved IP {reserved_ip}，属于账户 {token_key}"
                        )
                        
            except Exception as e:
                print(f"检查 token {token_key} 时出错: {e}")
                continue
        
        return error_response(
            f"Reserved IP {reserved_ip} 不存在或不属于任何已配置的账户",
            404,
            {
                "searched_accounts": list(tokens.keys()),
                "suggestion": "请检查 IP 地址是否正确"
            }
        )
        
    except Exception as e:
        return error_response(f"查找 Reserved IP 失败: {str(e)}")


@app.route('/api/cache/clear', methods=['POST'])
def clear_cache():
    """清除所有缓存"""
    try:
        doapi.clear_cache()
        return success_response(None, "缓存已清除")
    except Exception as e:
        return error_response(f"清除缓存失败: {str(e)}")


@app.route('/api/cache/status', methods=['GET'])
def get_cache_status():
    """获取缓存状态"""
    try:
        cache_info = {
            'cache_timeout': doapi._cache_timeout,
            'cached_items': len(doapi._cache),
            'cache_keys': list(doapi._cache.keys()) if doapi._cache else []
        }
        return success_response(cache_info, f"缓存中有 {len(doapi._cache)} 个项目")
    except Exception as e:
        return error_response(f"获取缓存状态失败: {str(e)}")


@app.route('/api/machines/<machine_name>/droplet-info', methods=['GET'])
@async_route
async def get_machine_info(machine_name):
    """获取指定机器的详细信息"""
    try:
        # 获取机器详情和对应的token
        machine_details = get_machine_details(machine_name)
        droplet_id = machine_details['id']
        token = machine_details['token']
        
        print(f"🔍 获取机器 {machine_name} 信息，使用 token: {machine_details['token_key']}")
        current_ip = await doapi.get_new_ip_for_droplet(token, droplet_id)
        
        # 获取所有droplets信息
        all_droplets = await doapi.get_droplets(token)
        machine_info = None
        
        for name, info in all_droplets.items():
            if info['id'] == droplet_id:
                machine_info = {
                    "name": name,
                    "id": info['id'],
                    "ips": info['v4_ips'],
                    "current_ip": current_ip,
                    "token_key": machine_details['token_key']  # 添加使用的token信息
                }
                break
        
        if machine_info:
            return success_response(
                machine_info, 
                f"获取机器 {machine_name} 信息成功"
            )
        else:
            return error_response(f"未找到机器 {machine_name} 的信息", 404)
            
    except Exception as e:
        return error_response(f"获取机器信息失败: {str(e)}")


async def find_machine_reserved_ip(token, droplet_id):
    """
    查找指定机器的 Reserved IP
    
    参数:
        token (str): DigitalOcean API token
        droplet_id (int): 机器的 Droplet ID
    
    返回:
        str or None: Reserved IP 地址，如果没有找到返回 None
    """
    try:
        reserved_ips = await doapi.get_reserved_ips(token)
        
        for reserved_ip in reserved_ips:
            droplet = reserved_ip.get('droplet')
            if droplet and droplet.get('id') == droplet_id:
                return reserved_ip.get('ip')
        
        return None
    except Exception as e:
        print(f"查找机器 Reserved IP 失败: {e}")
        return None


async def wait_for_reserved_ip_deletion(token, reserved_ip, max_wait_time=120):
    """
    等待 Reserved IP 删除完成
    
    参数:
        token (str): DigitalOcean API token
        reserved_ip (str): Reserved IP 地址
        max_wait_time (int): 最大等待时间（秒）
    
    返回:
        bool: 删除成功返回 True，超时返回 False
    """
    import time
    
    wait_time = 0
    while wait_time < max_wait_time:
        try:
            reserved_ips = await doapi.get_reserved_ips(token)
            
            # 检查 Reserved IP 是否还存在
            ip_exists = any(rip.get('ip') == reserved_ip for rip in reserved_ips)
            
            if not ip_exists:
                print(f"✅ Reserved IP {reserved_ip} 删除成功")
                return True
            
            print(f"⏳ 等待 Reserved IP {reserved_ip} 删除完成... ({wait_time}s/{max_wait_time}s)")
            await asyncio.sleep(10)  # 每10秒检查一次
            wait_time += 10
            
        except Exception as e:
            print(f"检查 Reserved IP 删除状态失败: {e}")
            await asyncio.sleep(10)
            wait_time += 10
    
    return False


async def wait_for_reserved_ip_creation(token, droplet_id, max_wait_time=120):
    """
    等待为指定机器创建的 Reserved IP 生效
    
    参数:
        token (str): DigitalOcean API token
        droplet_id (int): 机器的 Droplet ID
        max_wait_time (int): 最大等待时间（秒）
    
    返回:
        str or None: 新创建的 Reserved IP 地址，失败返回 None
    """
    import time
    
    wait_time = 0
    while wait_time < max_wait_time:
        try:
            # 查找机器的 Reserved IP
            new_reserved_ip = await find_machine_reserved_ip(token, droplet_id)
            
            if new_reserved_ip:
                print(f"✅ 新 Reserved IP {new_reserved_ip} 创建成功")
                return new_reserved_ip
            
            print(f"⏳ 等待新 Reserved IP 创建完成... ({wait_time}s/{max_wait_time}s)")
            await asyncio.sleep(10)  # 每10秒检查一次
            wait_time += 10
            
        except Exception as e:
            print(f"检查 Reserved IP 创建状态失败: {e}")
            await asyncio.sleep(10)
            wait_time += 10
    
    return None


@app.route('/api/machines/<machine_name>/replaceip', methods=['POST'])
@async_route
async def replace_reserved_ip(machine_name):
    """
    快捷更换机器的 Reserved IP（优化版本）
    
    流程：
    1. 查找机器当前的 Reserved IP
    2. 删除当前的 Reserved IP
    3. 等待删除完成（每10秒检查一次）
    4. 等待删除操作完全完成（10秒）
    5. 创建新的 Reserved IP（最多重试3次，递增等待时间）
    6. 等待创建完成（每10秒检查一次）
    7. 返回新的 Reserved IP 信息
    
    优化特性：
    - 删除后等待10秒确保操作完全完成
    - 创建失败时自动重试3次（15s, 30s, 45s间隔）
    - 详细的错误日志和控制台输出
    """
    try:
        # 获取机器详情和对应的token
        machine_details = get_machine_details(machine_name)
        droplet_id = machine_details['id']
        token = machine_details['token']
        
        print(f"🔄 开始为机器 {machine_name} (ID: {droplet_id}) 更换 Reserved IP，使用 token: {machine_details['token_key']}...")
        
        # 步骤1: 查找当前的 Reserved IP
        current_reserved_ip = await find_machine_reserved_ip(token, droplet_id)
        
        if not current_reserved_ip:
            return error_response(f"机器 {machine_name} 当前没有分配 Reserved IP，无法进行更换")
        
        print(f"📍 找到当前 Reserved IP: {current_reserved_ip}")
        
        # 步骤2: 删除当前的 Reserved IP
        print(f"🗑️ 开始删除 Reserved IP {current_reserved_ip}...")
        delete_success = await doapi.delete_reserved_ip(token, current_reserved_ip)
        
        if not delete_success:
            return error_response(f"删除当前 Reserved IP {current_reserved_ip} 失败")
        
        # 步骤3: 等待删除完成
        print("⏳ 等待 Reserved IP 删除完成...")
        delete_confirmed = await wait_for_reserved_ip_deletion(token, current_reserved_ip, max_wait_time=120)
        
        if not delete_confirmed:
            return error_response("删除 Reserved IP 超时，请稍后重试", 408)
        
        # 步骤4: 等待删除操作完全完成
        print("⏳ 等待删除操作完全完成...")
        await asyncio.sleep(10)  # 等待10秒确保删除操作完全完成
        
        # 步骤5: 创建新的 Reserved IP（带重试机制）
        print(f"🆕 开始为机器 {machine_name} 创建新的 Reserved IP...")
        
        max_retries = 3
        new_reserved_ip_info = None
        last_error = None
        
        for attempt in range(max_retries):
            try:
                print(f"🔄 创建 Reserved IP 尝试 {attempt + 1}/{max_retries}...")
                new_reserved_ip_info = await doapi.create_reserved_ip(token, droplet_id)
                
                # 检查是否成功创建
                if new_reserved_ip_info and not isinstance(new_reserved_ip_info, dict):
                    print(f"✅ 创建成功！")
                    break
                elif isinstance(new_reserved_ip_info, dict) and new_reserved_ip_info.get('error'):
                    error_type = new_reserved_ip_info.get('error')
                    error_message = new_reserved_ip_info.get('message', '未知错误')
                    last_error = new_reserved_ip_info
                    
                    print(f"❌ 创建失败: {error_message}")
                    
                    # 配额限制错误不需要重试
                    if error_type == 'quota_exceeded':
                        return error_response(
                            f"无法为机器 {machine_name} 创建 Reserved IP：已达到账户限制",
                            400,
                            {
                                "error_type": "quota_exceeded",
                                "suggestion": "请先删除其他不用的 Reserved IP，或联系 DigitalOcean 提升配额限制",
                                "original_message": error_message
                            }
                        )
                    
                    # 其他错误可以重试
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 15  # 递增等待时间：15s, 30s, 45s
                        print(f"⏳ {wait_time}秒后重试...")
                        await asyncio.sleep(wait_time)
                        continue
                else:
                    # 创建成功
                    break
                    
            except Exception as e:
                last_error = {"error": "exception", "message": str(e)}
                print(f"❌ 创建过程中出现异常: {str(e)}")
                
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 15
                    print(f"⏳ {wait_time}秒后重试...")
                    await asyncio.sleep(wait_time)
                    continue
        
        # 检查最终结果
        if not new_reserved_ip_info or (isinstance(new_reserved_ip_info, dict) and 'error' in new_reserved_ip_info):
            error_details = last_error or new_reserved_ip_info or {"message": "未知错误"}
            
            # 打印详细错误信息到控制台
            print(f"🚨 创建 Reserved IP 最终失败，已重试 {max_retries} 次")
            print(f"🔍 DigitalOcean 返回的错误详情:")
            print(f"   错误类型: {error_details.get('error', 'unknown')}")
            print(f"   错误消息: {error_details.get('message', '无详细信息')}")
            if 'status_code' in error_details:
                print(f"   状态码: {error_details.get('status_code')}")
            
            return error_response(
                f"为机器 {machine_name} 创建 Reserved IP 失败，已重试 {max_retries} 次",
                400,
                {
                    "error_type": "create_failed_after_retries",
                    "retries_attempted": max_retries,
                    "last_error": error_details,
                    "suggestion": "请稍后再试，或检查 DigitalOcean 账户状态"
                }
            )
        
        new_ip = new_reserved_ip_info.get('ip')
        print(f"📍 新 Reserved IP 创建请求已发送: {new_ip}")
        
        # 步骤6: 等待创建完成
        print("⏳ 等待新 Reserved IP 生效...")
        confirmed_new_ip = await wait_for_reserved_ip_creation(token, droplet_id, max_wait_time=120)
        
        if not confirmed_new_ip:
            return error_response("新 Reserved IP 创建超时，请检查 DigitalOcean 控制台", 408)
        
        # 步骤7: 返回成功结果
        result_data = {
            "machine_name": machine_name,
            "machine_id": droplet_id,
            "old_reserved_ip": current_reserved_ip,
            "new_reserved_ip": confirmed_new_ip,
            "status": "completed",
            "reserved_ip_info": new_reserved_ip_info
        }
        
        print(f"✅ Reserved IP 更换完成！{current_reserved_ip} -> {confirmed_new_ip}")
        
        return success_response(
            result_data,
            f"成功为机器 {machine_name} 更换 Reserved IP: {current_reserved_ip} -> {confirmed_new_ip}"
        )
        
    except Exception as e:
        print(f"❌ 更换 Reserved IP 失败: {e}")
        return error_response(f"更换 Reserved IP 失败: {str(e)}")


@app.errorhandler(404)
def not_found(error):
    """404错误处理"""
    return error_response("API端点不存在", 404)


@app.errorhandler(500)
def internal_error(error):
    """500错误处理"""
    return error_response("服务器内部错误", 500)


if __name__ == '__main__':
    print("🚀 Reserved IP 管理 API 启动中...")
    print("\n📋 可用的API端点:")
    print("  GET  /api/health                              - 健康检查")
    print("  GET  /api/machines                            - 获取机器列表")
    print("  GET  /api/reserved-ips                        - 获取所有账户的Reserved IP")
    print("  GET  /api/reserved-ips?simplify=true          - 获取简化版Reserved IP")
    print("  GET  /api/reserved-ips?token=do_token1        - 获取指定账户Reserved IP")
    print("  GET  /api/reserved-ips/summary                - 获取Reserved IP摘要")
    print("  GET  /api/reserved-ips/quota                  - 获取Reserved IP配额信息")
    print("  GET  /api/reserved-ips/all-accounts           - 获取所有账户Reserved IP")
    print("  POST /api/machines/<name>/reserved-ip         - 为机器创建Reserved IP")
    print("  POST /api/reserved-ips/<ip>/assign            - 分配Reserved IP")
    print("  POST /api/reserved-ips/<ip>/unassign          - 解除Reserved IP分配")
    print("  DELETE /api/reserved-ips/<ip>                 - 删除Reserved IP")
    print("  GET  /api/reserved-ips/<ip>/locate            - 查找Reserved IP所属账户")
    print("  POST /api/cache/clear                         - 清除缓存")
    print("  GET  /api/cache/status                        - 获取缓存状态")
    print("  GET  /api/machines/<name>/droplet-info        - 获取机器详情")
    print("  🔄 POST /api/machines/<name>/replaceip        - 快捷更换Reserved IP")
    print("\n🎯 支持的机器:")
    try:
        config = doapi.get_config()
        machines = config.get('machines', {})
        for machine_name, machine_config in machines.items():
            token_key = machine_config.get('usedo', 'do_token1')
            print(f"  - {machine_name} (使用 {token_key})")
    except:
        print("  - 无法读取机器配置")
    print("\n🌐 API服务运行在: http://localhost:5000")
    print("=" * 60)
    
    app.run(debug=True, host='0.0.0.0', port=5000)
