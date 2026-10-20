"""Local demo adapter. Replace the provider, not the HTTP contract, for SIS integration."""

from datetime import date, timedelta
from typing import Protocol
import time
from urllib.parse import urlparse

import httpx

from pydantic import BaseModel, Field, model_validator
from app.errors import APIError


class Course(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    date: date
    name: str = Field(min_length=1, max_length=200)
    start_time: str = Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    end_time: str = Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    location: str = Field(max_length=200)
    teacher: str = Field(max_length=100)

    @model_validator(mode='after')
    def valid_times(self):
        if self.end_time <= self.start_time:
            raise ValueError('课程结束时间必须晚于开始时间')
        return self


class ScheduleProvider(Protocol):
    source: str
    async def get_schedule(self, student_id: str, start: date, end: date, tenant_id: str = 'local-demo') -> list[Course]: ...


class MockScheduleProvider:
    source = 'mock'

    async def get_schedule(self, student_id: str, start: date, end: date, tenant_id: str = 'local-demo') -> list[Course]:
        courses = []
        day = start
        while day <= end:
            if day.weekday() < 5:
                courses.append(Course(
                    id=f"{day.isoformat()}-1", date=day, name="程序设计基础",
                    start_time="09:00", end_time="10:35", location="教学楼 A-201",
                    teacher="模拟教师",
                ))
            day += timedelta(days=1)
        return courses


class HttpScheduleProvider:
    """Campus adapter contract; no real school API is assumed to have this shape."""
    source = 'sis'

    def __init__(self, options, client=None):
        self.url = options.get('url', '')
        parsed = urlparse(self.url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise APIError(503, 'SIS_CONFIGURATION', '真实教务接口必须配置不含账号密码的 HTTPS URL。')
        self.token = options.get('token', '')
        if not isinstance(self.token, str) or not self.token:
            raise APIError(503, 'SIS_CONFIGURATION', '请配置教务服务端访问令牌。')
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(3, connect=2),
            follow_redirects=False, trust_env=False,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))
        self.failures = 0
        self.retry_at = 0.0

    async def aclose(self):
        await self.client.aclose()

    async def get_schedule(self, student_id, start, end, tenant_id='local-demo'):
        if time.monotonic() < self.retry_at:
            raise APIError(503, 'SIS_UNAVAILABLE', '教务服务暂不可用，请稍后重试。')
        try:
            async with self.client.stream('GET', self.url, params={'student_id': student_id,
                'tenant_id': tenant_id, 'start_date': start.isoformat(), 'end_date': end.isoformat()},
                headers={'Authorization': 'Bearer ' + self.token}) as response:
                if response.status_code == 404:
                    raise APIError(409, 'IDENTITY_NOT_LINKED', '教务系统未找到关联账号。')
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 1_000_000:
                        raise ValueError('Response too large')
            import json
            payload = json.loads(body)
            if payload.get('student_id') != student_id or payload.get('tenant_id') != tenant_id:
                raise ValueError('Identity mismatch')
            if not isinstance(payload.get('courses'), list) or len(payload['courses']) > 500:
                raise ValueError('Invalid courses')
            courses = [Course.model_validate(item) for item in payload['courses']]
            if any(not start <= item.date <= end for item in courses):
                raise ValueError('Invalid range')
            if len({item.id for item in courses}) != len(courses):
                raise ValueError('Duplicate course IDs')
            self.failures = 0
            return sorted(courses, key=lambda item: (item.date, item.start_time, item.id))
        except APIError:
            raise
        except Exception as exc:
            self.failures += 1
            if self.failures >= 3:
                self.retry_at = time.monotonic() + 10
            raise APIError(503, 'SIS_UNAVAILABLE', '教务查询超时、异常或返回了无效数据，请稍后重试。') from exc
