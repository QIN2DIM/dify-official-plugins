# -*- coding: utf-8 -*-
"""
@Time    : 2025/9/19 14:03
@Author  : QIN2DIM
@GitHub  : https://github.com/QIN2DIM
@Desc    : 
"""
import json
import os

import dotenv
import httpx

dotenv.load_dotenv()

base_url = os.environ.get("GITLAB_BASE_URL", "https://gitlab.com/api/v4")
access_token = os.environ["GITLAB_ACCESS_TOKEN"]
print(base_url, access_token)

client = httpx.Client(base_url=base_url)


def _get_projects(max_projects: int = 100):
    """获取用户项目列表"""
    params = {
        "per_page": max_projects,
        "order_by": "last_activity_at",
        "sort": "desc",
        "membership": True  # 只获取用户有权限的项目
    }
    response = client.get(f"/projects", params=params)
    print(response.status_code)
    projects = response.json()
    for project in projects:
        print(json.dumps(project, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    _get_projects()
