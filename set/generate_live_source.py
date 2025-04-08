import requests

def generate_live_source():
    # 原始直播源地址
    original_url = "https://raw.githubusercontent.com/gzj7003/tvlist/refs/heads/main/txt_files/Susaw.txt"
    
    # 苏州直播源内容
    suzhou_sources = """苏州新闻综合,https://live-auth.51kandianshi.com/szgd/csztv1.m3u8$江苏苏州地方
苏州社会经济,https://live-auth.51kandianshi.com/szgd/csztv2.m3u8$江苏苏州地方
苏州文化生活,https://live-auth.51kandianshi.com/szgd/csztv3.m3u8$江苏苏州地方
苏州生活资讯,https://live-auth.51kandianshi.com/szgd/csztv5.m3u8$江苏苏州地方
苏州4K,https://live-auth.51kandianshi.com/szgd/csztv4k_hd.m3u8$江苏苏州地方"""

    try:
        # 获取原始直播源内容
        response = requests.get(original_url)
        response.raise_for_status()  # 检查请求是否成功
        
        # 合并内容
        combined_content = response.text + "\n" + suzhou_sources
        
        # 写入到新文件
        with open("Susaw-sz.txt", "w", encoding="utf-8") as f:
            f.write(combined_content)
            
        print("直播源文件已成功生成：Susaw-sz.txt")
        
    except requests.exceptions.RequestException as e:
        print(f"获取原始直播源时出错: {e}")
    except Exception as e:
        print(f"发生错误: {e}")

if __name__ == "__main__":
    generate_live_source()
