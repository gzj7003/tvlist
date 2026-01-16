import os
import requests
import re
import base64
import cv2
import datetime
import time
import random
import json
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urlparse

# ==================== 配置文件 ====================
class Config:
    # 从环境变量获取FOFA账号信息（如果有的话）
    FOFA_EMAIL = os.getenv('FOFA_EMAIL', '')
    FOFA_TOKEN = os.getenv('FOFA_TOKEN', '')
    
    # 请求配置
    REQUEST_DELAY_MIN = 5  # 最小延迟(秒)
    REQUEST_DELAY_MAX = 12  # 最大延迟(秒)
    MAX_RETRIES = 3  # 最大重试次数
    REQUEST_TIMEOUT = 15  # 请求超时时间
    
    # User-Agent列表（轮换使用）
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
    ]
    
    # Referer列表
    REFERERS = [
        "https://www.google.com/",
        "https://www.baidu.com/",
        "https://www.bing.com/",
        "https://fofa.info/"
    ]

# ==================== ISP映射字典 ====================
ISP_MAPPING = {
    "北京联通": {"org": "China Unicom Beijing Province Network", "isp_en": "cucc"},
    "联通": {"org": "CHINA UNICOM China169 Backbone", "isp_en": "cucc"},
    "电信": {"org": "Chinanet", "isp_en": "ctcc"},
    "移动": {"org": "China Mobile communications corporation", "isp_en": "cmcc"}
}

# ==================== 工具函数 ====================
def get_random_headers():
    """获取随机的请求头"""
    return {
        "Referer": random.choice(Config.REFERERS),
        "User-Agent": random.choice(Config.USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0"
    }

def make_request_with_retry(url, max_retries=Config.MAX_RETRIES):
    """带重试机制的请求函数"""
    for attempt in range(max_retries):
        try:
            # 每次重试前增加延迟
            if attempt > 0:
                retry_delay = Config.REQUEST_DELAY_MIN * (attempt + 1)
                print(f"等待 {retry_delay} 秒后重试...")
                time.sleep(retry_delay)
            
            headers = get_random_headers()
            response = requests.get(
                url, 
                headers=headers, 
                timeout=Config.REQUEST_TIMEOUT,
                allow_redirects=True
            )
            
            # 检查是否被反爬
            response_text = response.text
            if "[-3000]" in response_text or "IP访问异常" in response_text:
                print(f"第{attempt+1}次请求被反爬机制拦截")
                if "登录账号可用" in response_text and Config.FOFA_EMAIL:
                    print("检测到需要登录，尝试使用API方式...")
                    return None  # 返回None让调用者知道需要使用API
                continue  # 继续重试
            
            response.raise_for_status()  # 检查HTTP状态码
            return response
            
        except requests.exceptions.Timeout:
            print(f"第{attempt+1}次请求超时")
            if attempt == max_retries - 1:
                raise
                
        except requests.exceptions.RequestException as e:
            print(f"第{attempt+1}次请求失败: {e}")
            if attempt == max_retries - 1:
                raise
    
    return None

def fofa_api_search(search_txt):
    """使用FOFA API进行搜索"""
    if not Config.FOFA_EMAIL or not Config.FOFA_TOKEN:
        print("未设置FOFA API凭据，无法使用API搜索")
        return None
    
    try:
        api_url = "https://fofa.info/api/v1/search/all"
        params = {
            "email": Config.FOFA_EMAIL,
            "key": Config.FOFA_TOKEN,
            "qbase64": search_txt,
            "size": 100,
            "fields": "host,title,ip,domain,port,country,city"
        }
        
        response = requests.get(
            api_url, 
            params=params, 
            timeout=Config.REQUEST_TIMEOUT
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("error"):
                print(f"API错误: {data.get('errmsg')}")
                return None
            return data.get("results", [])
        else:
            print(f"API请求失败: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"API请求异常: {e}")
        return None

def validate_video_stream(url, mcast):
    """验证视频流是否有效"""
    try:
        video_url = f"{url}/rtp/{mcast}"
        cap = cv2.VideoCapture(video_url)
        
        # 设置超时时间（毫秒）
        cap.set(cv2.CAP_PROP_POS_MSEC, 3000)
        
        if not cap.isOpened():
            return False, None
            
        # 尝试读取几帧
        ret, frame = cap.read()
        if not ret:
            cap.release()
            return False, None
            
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        cap.release()
        
        if width > 0 and height > 0:
            return True, f"{width}x{height}"
        else:
            return False, None
            
    except Exception as e:
        print(f"验证视频流时出错: {e}")
        return False, None

def process_province_isp(province, isp, mcast, current_time):
    """处理单个省份ISP"""
    print(f"\n{'='*50}")
    print(f"开始处理: {province}{isp} (组播: {mcast})")
    print(f"{'='*50}")
    
    # 获取运营商信息
    isp_key = f"{province}{isp}" if province == "北京" and isp == "联通" else isp
    isp_info = ISP_MAPPING.get(isp_key, ISP_MAPPING.get(isp, {}))
    
    if not isp_info:
        print(f"未找到 {isp} 的运营商信息，跳过")
        return []
    
    org = isp_info["org"]
    
    # 构建搜索查询
    search_txt = f'\"udpxy\" && country=\"CN\" && region=\"{province}\" && org=\"{org}\"'
    search_txt_b64 = base64.b64encode(search_txt.encode('utf-8')).decode('utf-8')
    search_url = f'https://fofa.info/result?qbase64={search_txt_b64}'
    
    print(f"{current_time} 查询运营商: {province}{isp}")
    print(f"搜索URL: {search_url}")
    
    result_urls = set()
    
    # 优先尝试API搜索
    if Config.FOFA_EMAIL and Config.FOFA_TOKEN:
        print("尝试使用FOFA API搜索...")
        results = fofa_api_search(search_txt_b64)
        if results:
            for result in results:
                host = result.get('host', '')
                if host.startswith('http://'):
                    result_urls.add(host)
            print(f"API搜索找到 {len(result_urls)} 个结果")
    
    # 如果API搜索失败或无结果，尝试网页爬取
    if not result_urls:
        print("尝试网页爬取...")
        response = make_request_with_retry(search_url)
        
        if response and response.status_code == 200:
            html_content = response.text
            
            # 使用正则表达式提取URL
            pattern = r"http://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+"
            urls_all = re.findall(pattern, html_content)
            result_urls = set(urls_all)
            print(f"网页爬取找到 {len(result_urls)} 个结果")
    
    if not result_urls:
        print(f"未找到 {province}{isp} 的有效结果")
        return []
    
    # 验证视频流
    print(f"开始验证 {len(result_urls)} 个URL...")
    valid_ips = []
    
    for url in result_urls:
        print(f"验证: {url}")
        is_valid, resolution = validate_video_stream(url, mcast)
        if is_valid:
            print(f"  ✓ 有效 (分辨率: {resolution})")
            valid_ips.append(url)
        else:
            print(f"  ✗ 无效")
    
    print(f"验证完成，有效URL: {len(valid_ips)}/{len(result_urls)}")
    return valid_ips

def main():
    """主函数"""
    print("="*60)
    print("IPTV频道源自动生成脚本")
    print("="*60)
    
    # 检查目录
    if not os.path.exists('udpjs'):
        print("错误: udpah 目录不存在")
        return
    
    if not os.path.exists('txt_files'):
        os.makedirs('txt_files')
        print("创建 txt_files 目录")
    
    # 获取省份ISP列表
    files = [f for f in os.listdir('udpjs') if f.endswith('.txt')]
    provinces_isps = []
    keywords = []
    
    for file in files:
        name = os.path.splitext(file)[0]
        if name.count('_') == 1:
            provinces_isps.append(name)
    
    print(f"找到 {len(provinces_isps)} 个省份ISP配置")
    
    # 解析关键词
    for province_isp in provinces_isps:
        try:
            file_path = f'udpjs/{province_isp}.txt'
            with open(file_path, 'r', encoding='utf-8') as f:
                first_line = f.readline().strip()
                
            if "rtp://" in first_line:
                mcast = first_line.split("rtp://")[1].split(" ")[0]
                keywords.append(f"{province_isp}_{mcast}")
                print(f"解析: {province_isp} -> 组播: {mcast}")
        except Exception as e:
            print(f"解析 {province_isp} 时出错: {e}")
    
    if not keywords:
        print("未找到有效的关键词，请检查udpah目录下的文件")
        return
    
    print(f"\n开始处理 {len(keywords)} 个查询...")
    
    # 处理每个关键词
    total_processed = 0
    total_success = 0
    
    for i, keyword in enumerate(keywords):
        try:
            # 在请求之间添加随机延迟（第一个请求除外）
            if i > 0:
                delay = random.uniform(Config.REQUEST_DELAY_MIN, Config.REQUEST_DELAY_MAX)
                print(f"\n等待 {delay:.1f} 秒后继续...")
                time.sleep(delay)
            
            parts = keyword.split("_")
            if len(parts) != 3:
                print(f"关键词格式错误: {keyword}")
                continue
                
            province, isp, mcast = parts
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            total_processed += 1
            
            # 处理省份ISP
            valid_ips = process_province_isp(province, isp, mcast, current_time)
            
            if valid_ips:
                total_success += 1
                # 读取原始文件内容
                rtp_filename = f'udpjs/{province}_{isp}.txt'
                try:
                    with open(rtp_filename, 'r', encoding='utf-8') as f:
                        original_content = f.read()
                    
                    # 生成新的播放列表
                    txt_filename = f'txt_files/{province}{isp}.txt'
                    with open(txt_filename, 'w', encoding='utf-8') as f:
                        for url in valid_ips:
                            new_content = original_content.replace("rtp://", f"{url}/rtp/")
                            f.write(new_content)
                        
                        # 添加额外的频道
                        extra_channels = [
                            "\n# 苏州新闻综合\n",
                            "苏州新闻综合,https://live-auth.51kandianshi.com/szgd/csztv1.m3u8\n",
                            "\n# 苏州社会经济\n",
                            "苏州社会经济,https://live-auth.51kandianshi.com/szgd/csztv2.m3u8\n",
                            "\n# 苏州文化生活\n",
                            "苏州文化生活,https://live-auth.51kandianshi.com/szgd/csztv3.m3u8\n",
                            "\n# 苏州生活资讯\n",
                            "苏州生活资讯,https://live-auth.51kandianshi.com/szgd/csztv5.m3u8\n",
                            "\n# 苏州4K\n",
                            "苏州4K,https://live-auth.51kandianshi.com/szgd/csztv4k_hd.m3u8\n"
                        ]
                        
                        for channel in extra_channels:
                            f.write(channel)
                    
                    print(f"✓ 已生成播放列表: {txt_filename}")
                    
                except Exception as e:
                    print(f"生成文件时出错: {e}")
            else:
                print(f"✗ {province}{isp} 未找到有效IP")
                
        except Exception as e:
            print(f"处理 {keyword} 时发生错误: {e}")
            continue
    
    print("\n" + "="*60)
    print(f"处理完成!")
    print(f"总计: {total_processed} 个查询")
    print(f"成功: {total_success} 个")
    print(f"失败: {total_processed - total_success} 个")
    print(f"文件输出在 txt_files 目录下")
    print("="*60)

if __name__ == "__main__":
    main()
