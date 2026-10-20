import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.modules.intent.application_router import ResolveApplication, resolve_application
from app.modules.intent.router import resolve
from datetime import date
from support import student_headers


APPS = [
    {'id': 'schedule', 'name': '课表助手', 'type_id': 'campus.course-query'},
    {'id': '11111111-1111-4111-8111-111111111111', 'name': '本科课表', 'type_id': 'campus.course-query'},
]


class ApplicationRoutingTests(unittest.TestCase):
    def route(self, text, current=None, selected=None, admin=False, apps=None):
        return resolve_application(ResolveApplication(text=text, current_application_id=current,
            selected_application_id=selected), APPS if apps is None else apps, admin)

    def test_no_fixed_schedule_default_and_candidate_selection(self):
        result = self.route('明天课表')
        self.assertEqual(result.status, 'clarification')
        self.assertEqual(len(result.candidates), 2)
        self.assertIsNone(result.application_id)
        picked = self.route('明天课表', selected=APPS[1]['id'])
        self.assertEqual(picked.status, 'query')
        self.assertEqual(picked.application_id, APPS[1]['id'])
        generic_name = [APPS[0], {**APPS[1], 'name': '明天课表'}]
        self.assertEqual(self.route('明天课表', apps=generic_name).status, 'clarification')
        self.assertEqual(self.route('明天课表', current='schedule', apps=generic_name).application_id, 'schedule')

    def test_search_filters_authorized_catalog_without_running_business(self):
        result = self.route('搜索应用本科', current='schedule')
        self.assertEqual(result.status, 'reset')
        self.assertEqual([item.id for item in result.candidates], [APPS[1]['id']])
        self.assertIsNone(result.query_text)
        chosen = self.route('搜索应用本科', selected=APPS[1]['id'])
        self.assertEqual(chosen.status, 'selected')
        self.assertEqual(chosen.application_id, APPS[1]['id'])
        invalid = self.route('搜索应用本科', selected='schedule')
        self.assertEqual(invalid.status, 'clarification')
        self.assertIsNone(invalid.application_id)
        self.assertEqual(self.route('查找应用不存在').candidates, [])

    def test_search_words_are_not_interpreted_as_management_operations(self):
        apps = [{'id': 'visible', 'name': '校园事务助手', 'description': '校园日常管理'}]
        result = self.route('搜索应用管理', apps=apps)
        self.assertEqual(result.status, 'reset')
        self.assertEqual(len(result.candidates), 1)
        picked = self.route('搜索应用管理', apps=apps, selected='visible')
        self.assertEqual(picked.status, 'selected')
        self.assertIsNone(picked.target)

    def test_feature_actions_are_scoped_text_commands_with_role_limits(self):
        selected = self.route('打开本科课表')
        self.assertEqual({action.id for action in selected.feature_actions}, {'query-today', 'query-week', 'show-features'})
        today = next(action for action in selected.feature_actions if action.id == 'query-today')
        follow = self.route(today.text, current='schedule', selected=selected.application_id)
        self.assertEqual(follow.application_id, APPS[1]['id'])
        self.assertEqual(follow.status, 'query')
        admin = self.route('打开本科课表', admin=True)
        manage = next(action for action in admin.feature_actions if action.id == 'configure-questions')
        navigation = self.route(manage.text, selected=admin.application_id, admin=True)
        self.assertEqual(navigation.tab, 'questions')
        self.assertEqual(navigation.status, 'navigation')
        self.assertEqual(self.route(manage.text, selected=admin.application_id).status, 'unsupported')

    def test_empty_catalog_explains_that_no_application_is_available(self):
        empty = self.route('有哪些应用', apps=[])
        self.assertEqual(empty.candidates, [])
        self.assertIn('暂无可用应用', empty.message)
        self.assertIsNone(self.route('明天课表', apps=[]).query_text)

    def test_named_selection_switch_and_follow_up(self):
        selected = self.route('打开本科课表', current='schedule')
        self.assertEqual(selected.status, 'selected')
        self.assertEqual(selected.application_id, APPS[1]['id'])
        direct = self.route('请用本科课表查询明天课表', current='schedule')
        self.assertEqual(direct.status, 'query')
        self.assertEqual(direct.query_text, '查询明天课表')
        self.assertEqual(resolve(direct.query_text, date(2026, 10, 20)).status, 'resolved')
        follow_up = self.route('明天呢', current=APPS[1]['id'])
        self.assertEqual(follow_up.query_text, '明天课表')
        self.assertEqual(follow_up.application_id, APPS[1]['id'])

    def test_unknown_explicit_app_never_reuses_previous_application(self):
        result = self.route('打开不存在的应用查明天课表', current='schedule')
        self.assertEqual(result.status, 'clarification')
        self.assertIsNone(result.application_id)
        self.assertEqual(self.route('明天呢', current='hidden-app').status, 'clarification')
        self.assertEqual(self.route('明天课表', selected='hidden-app').status, 'clarification')

    def test_nested_names_duplicate_names_and_multiple_mentions(self):
        apps = [APPS[1], {'id': 'short', 'name': '课表'}]
        self.assertEqual(self.route('打开本科课表', apps=apps).application_id, APPS[1]['id'])
        self.assertEqual(self.route('本科课表和课表都查一下', apps=apps).status, 'clarification')
        duplicate_names = [APPS[1], {**APPS[1], 'id': 'another'}]
        self.assertEqual(len(self.route('打开本科课表', apps=duplicate_names).candidates), 2)
        repeated_ids = [APPS[1], APPS[1]]
        self.assertEqual(self.route('打开本科课表', apps=repeated_ids).status, 'selected')

    def test_management_routes_are_authorized_navigation_only(self):
        self.assertEqual(self.route('配置本科课表').status, 'unsupported')
        for text, tab in [('配置本科课表', 'basic'), ('设置本科课表页面字段', 'page'),
                ('管理本科课表问法', 'questions'), ('查看本科课表发布记录', 'history')]:
            result = self.route(text, admin=True)
            self.assertEqual(result.status, 'navigation')
            self.assertEqual(result.application_id, APPS[1]['id'])
            self.assertEqual(result.tab, tab)
        self.assertEqual(self.route('打开应用工作台').status, 'unsupported')
        self.assertEqual(self.route('打开应用工作台', admin=True).target, 'admin')
        self.assertEqual(self.route('删除本科课表', admin=True).status, 'navigation')

    def test_names_containing_management_words_are_not_management_commands(self):
        named = [{'id': 'valid', 'name': '课程管理助手'}]
        self.assertEqual(self.route('打开课程管理助手', apps=named).status, 'selected')
        self.assertEqual(self.route('课程管理助手明天课表', apps=named).status, 'query')
        self.assertEqual(self.route('打开别样课程', apps=[{'id': 'valid', 'name': '别样课程'}]).status, 'selected')
        alias = [{**APPS[1], 'aliases': ['旧课表名称']}]
        self.assertEqual(self.route('配置旧课表名称', apps=alias, admin=True).application_id, APPS[1]['id'])

    def test_discovery_reset_and_select_do_not_execute_query(self):
        reset = self.route('有哪些应用', current='schedule')
        self.assertEqual(reset.status, 'reset')
        selected = self.route('有哪些应用', selected=APPS[1]['id'])
        self.assertEqual(selected.status, 'selected')
        self.assertEqual(selected.application_id, APPS[1]['id'])
        self.assertEqual(self.route('这个应用有哪些功能', current=APPS[1]['id']).status, 'selected')

    def test_unsafe_operations_do_not_execute(self):
        for text in ['不要打开本科课表', '本科课表帮我采购', '本科课表审批申请', '本科课表执行命令']:
            self.assertEqual(self.route(text).status, 'unsupported')
        self.assertEqual(self.route('管理本科课表的数据', admin=True).status, 'unsupported')
        self.assertEqual(self.route('明天和后天课表', current='schedule').query_text, '明天和后天课表')
        self.assertEqual(resolve(self.route('明天和后天课表', current='schedule').query_text, date(2026, 10, 20)).status, 'clarification')

    def test_unpublished_admin_application_guides_to_configuration(self):
        draft = [{**APPS[1], 'usable': False}]
        result = self.route('打开本科课表', admin=True, apps=draft)
        self.assertEqual(result.status, 'navigation')
        self.assertIsNone(result.query_text)

    def test_http_auth_visibility_and_no_model_or_business_call(self):
        client = TestClient(app)
        headers = student_headers(self)
        data = {'applications': [{'id': a['id'], 'name': a['name'], 'ready': True,
            'category': '学习服务', 'desc': '查询本人课程'} for a in APPS] + [
                {'id': 'planned', 'name': '请假服务', 'ready': False, 'category': '校园事务', 'desc': '未接入'}]}
        with patch('app.modules.config.applications.catalog', return_value=data), patch(
                'app.modules.config.ai_settings.active_model') as model, patch('app.main.fetch_schedule') as business:
            self.assertEqual(client.post('/api/v1/assistant/resolve', json={'text': '有哪些应用'}).status_code, 401)
            result = client.post('/api/v1/assistant/resolve', headers=headers, json={'text': '有哪些应用'})
            self.assertEqual(result.status_code, 200)
            self.assertEqual({a['id'] for a in result.json()['candidates']}, {a['id'] for a in APPS})
            rejected = client.post('/api/v1/assistant/resolve', headers=headers,
                json={'text': '明天课表', 'selected_application_id': 'not-authorized'})
            self.assertEqual(rejected.json()['status'], 'clarification')
            model.assert_not_called()
            business.assert_not_called()

    def test_admin_http_navigation_is_scoped_to_manageable_catalog(self):
        client = TestClient(app)
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}), patch(
                'app.modules.config.applications.catalog', return_value={'applications': []}), patch(
                'app.modules.config.routes.execute', side_effect=lambda operation, principal: [
                    {'application_id': APPS[1]['id'], 'name': '本科课表', 'type_id': 'campus.course-query',
                     'legacy': False, 'current_release_id': 'published'}]):
            result = client.post('/api/v1/assistant/resolve', headers={'Authorization': 'Bearer ' + 'a' * 24},
                json={'text': '配置本科课表的问法'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['tab'], 'questions')
            self.assertEqual(result.json()['application_id'], APPS[1]['id'])
