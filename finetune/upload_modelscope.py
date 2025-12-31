import argparse
import os
from modelscope.hub.api import HubApi
from modelscope import AutoConfig

def main():
    # 1. 解析命令行参数
    parser = argparse.ArgumentParser(description='上传模型到ModelScope')
    parser.add_argument('--model-id', required=True, help='ModelScope仓库名，格式：用户名/仓库名')
    parser.add_argument('--model-dir', required=True, help='本地模型目录路径')
    parser.add_argument('--token', default='', help='ModelScope访问令牌（可选，也可通过终端登录）')
    args = parser.parse_args()

    # 2. 初始化API并强制登录
    api = HubApi()
    if args.token:
        api.login(token=args.token)
        print("✅ 通过token登录ModelScope成功！")
    else:
        try:
            api.login()
            print("✅ 终端登录状态验证成功！")
        except Exception as e:
            print(f"❌ 登录失败：{e}")
            print("请执行以下操作之一：")
            print("1. 终端执行 modelscope login 并输入令牌")
            print("2. 运行脚本时加 --token 参数：python upload_modelscope.py --model-id xxx --model-dir xxx --token 你的令牌")
            return 1

    # 3. 检查并生成配置文件
    config_path = os.path.join(args.model_dir, 'configuration.json')
    if not os.path.exists(config_path):
        print(f"⚠️  未找到 configuration.json，尝试从模型目录生成...")
        try:
            config = AutoConfig.from_pretrained(args.model_dir, trust_remote_code=True)
            config.save_pretrained(args.model_dir)
            print(f"✅ 已自动生成 configuration.json 到 {args.model_dir}")
        except Exception as e:
            print(f"⚠️  自动生成配置文件失败：{e}")
            print("请确保模型目录包含 config.json 或 configuration.json")

    # 4. 最终版：仅保留核心参数，移除所有无效参数（ignore_file/upload_mode）
    try:
        api.upload_folder(
            repo_id=args.model_id,          # 仅保留：仓库名
            folder_path=args.model_dir,     # 仅保留：本地模型目录
            repo_type='model',              # 仅保留：指定为模型仓库
            revision='master'               # 仅保留：分支名（默认master）
            # 所有易兼容问题的参数全部移除，确保适配所有版本
        )
        print(f"\n🎉 模型上传成功！")
        print(f"ModelScope仓库地址：https://www.modelscope.cn/models/{args.model_id}")
        return 0
    except Exception as e:
        print(f"\n❌ 上传失败：{e}")
        print("\n💡 提示：如果是大文件上传中断，直接重新运行本脚本即可（自动断点续传）")
        # 额外排查建议
        print("\n🔍 常见排查方向：")
        print("1. 检查模型目录是否存在：", os.path.exists(args.model_dir))
        print("2. 检查模型目录下的文件：")
        for f in os.listdir(args.model_dir)[:10]:  # 打印前10个文件
            print(f"   - {f}")
        return 1

if __name__ == '__main__':
    raise SystemExit(main())