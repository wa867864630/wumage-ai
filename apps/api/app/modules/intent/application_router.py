"""Authorized application discovery before dispatching a business operation. No model calls/writes."""
import re
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.modules.identity.auth import current_user
from app.modules.intent.router import normalize, resolve as resolve_course, campus_today

router = APIRouter(prefix='/api/v1/assistant', tags=['校园助手应用路由'])


class ResolveApplication(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=200)
    current_application_id: str | None = Field(default=None, max_length=36)
    selected_application_id: str | None = Field(default=None, max_length=36)
    construction_subject: str | None = Field(default=None, max_length=80)


class Candidate(BaseModel):
    id: str
    name: str
    category: str = '学习服务'
    description: str = ''


class FeatureAction(BaseModel):
    id: str
    label: str
    text: str


class ApplicationResolution(BaseModel):
    status: Literal['query', 'selected', 'clarification', 'navigation', 'unsupported', 'reset']
    message: str
    application_id: str | None = None
    application_name: str | None = None
    query_text: str | None = None
    candidates: list[Candidate] = []
    features: list[str] = []
    feature_actions: list[FeatureAction] = []
    target: Literal['admin'] | None = None
    tab: Literal['basic', 'page', 'questions', 'history'] = 'basic'
    construction_subject: str | None = None
    type_id: str | None = None
    runtime_action: Literal['fill', 'records'] | None = None


def key(value):
    return normalize(value).casefold()


def names(application):
    return sorted({key(value) for value in [application['name'], *application.get('aliases', [])] if key(value)}, key=len, reverse=True)


def strip_names(text, application):
    return re.sub(r'[《「“【]?(?:'+'|'.join(re.escape(name) for name in names(application))+r')[》」”】]?', '', text)


def name_matches(text, applications):
    """Nested names do not make one mention ambiguous; separate mentions still do."""
    spans = [(match.start(), match.end(), app) for app in applications for name in names(app)
        for match in re.finditer(re.escape(name), text)]
    ids = {app['id'] for start, end, app in spans if not any(
        left <= start and end <= right and (left < start or end < right) for left, right, _ in spans)}
    return [app for app in applications if app['id'] in ids]


def resolve_application(command, applications, admin=False):
    # Only the server-authorized catalog may be supplied here. Never use client catalog metadata.
    applications = list({app['id']: app for app in applications}.values())
    # Choosing an existing candidate from a creation guide means inspect/use that app,
    # never dispatch the original creation sentence as a course/model query.
    if command.selected_application_id and re.search(r'创建|新建|搭建', key(command.text)):
        command = command.model_copy(update={'text': '这个应用有哪些功能'})
    text = key(command.text)
    search = re.fullmatch(r'(?:请|帮我)?(?:搜索|查找|寻找)(?:一下)?(?:应用)?[：: ]*(.*)', text)
    if search:
        keyword = search[1].strip('《》「」“”【】')
        applications = [a for a in applications if not keyword or any(keyword in value for value in [
            *names(a), key(a.get('description', '')), key(a.get('category', ''))])]
    choices = [Candidate(id=a['id'], name=a['name'], category=a.get('category', '学习服务'),
        description=a.get('description', '')) for a in applications]
    discovery = bool(search) or text in {'取消锁定', '退出当前应用', '返回校园助手', '切换应用', '换个应用', '有哪些应用', '应用列表', '有什么应用', '有哪些可用应用', '可用应用', '选择一个应用'}
    if discovery and not command.selected_application_id:
        message = '找到以下应用，请选择后使用。' if search else '请选择要使用的应用，也可以直接输入应用名称。'
        if not choices:
            message = '没有找到可访问的匹配应用，可以换个关键词或说“有哪些应用”。' if search else '当前身份暂无可用应用，请联系管理员确认发布状态和访问范围。'
        return ApplicationResolution(status='reset', message=message, candidates=choices)
    matches = name_matches(text, applications)
    # Cross-business requests must not fall through to the currently selected course application.
    # This is a narrow declared vocabulary, not model-generated executable capability discovery.
    construction = bool(re.search(r'创建|新建|搭建', text))
    subject_match = re.search(r'请假|采购|场地预约|访问申请|活动报名', text)
    course_construction = construction and bool(re.search(r'课表|课程', text))
    form_construction = construction and bool(re.search(r'登记|表单|填表', text))
    semantic_matches = False
    if not construction and not matches:
        wanted = 'platform.leave-approval' if subject_match and subject_match[0] == '请假' else 'platform.form' if re.search(r'登记|填表|表单', text) else None
        current_app = next((app for app in applications if app['id'] == command.current_application_id), None)
        if (not wanted and not command.selected_application_id and current_app
                and current_app.get('type_id') in ('platform.form', 'platform.leave-approval')
                and re.search(r'课表|课程|上课', text)
                and not re.search(r'打开|进入|切换|(?:用|通过|在).+(?:应用|助手|服务)', text)
                and resolve_course(text, campus_today()).status == 'resolved'):
            wanted = 'campus.course-query'
        if wanted:
            eligible = [app for app in applications if app.get('type_id', 'campus.course-query') == wanted]
            current = next((app for app in eligible if app['id'] == command.current_application_id), None)
            matches = [current] if current else eligible
            semantic_matches = bool(matches)
    if (not matches or course_construction or form_construction) and not command.selected_application_id and (subject_match or construction):
        if re.search(r'不要|别|不想|无需|取消', text):
            return ApplicationResolution(status='unsupported', message='没有创建应用或保存建设需求。')
        reference = re.fullmatch(r'(?:请|帮我|我要|我想)?(?:创建|新建|搭建)(?:这个|该)应用[。！! ]*', text)
        subject = subject_match[0] if subject_match else '课程查询' if course_construction else '简单登记' if form_construction else command.construction_subject if reference else None
        if not subject:
            if admin:
                return ApplicationResolution(status='navigation', target='admin', message='请在应用工作台选择已安装类型与模板。尚未安装的业务，请先描述具体需求。')
            return ApplicationResolution(status='unsupported', message='应用建设需要管理员处理，你可以使用已授权的应用。')
        if not admin:
            return ApplicationResolution(status='unsupported', message=f'当前没有可访问的“{subject}”服务，请联系管理员确认服务与访问范围。')
        from app.modules.approvals.adapter import enabled as approvals_enabled
        leave_available = subject == '请假' and approvals_enabled()
        return ApplicationResolution(status='clarification', construction_subject=subject,
            message='可以先完善登记建设需求，再选择已启用模板、确认创建并配置发布；也可以选择已有登记应用。' if subject == '简单登记' else
                '课程查询类型已安装。可以先完善建设需求，再选择模板、确认创建并配置发布；也可以继续使用已有课程应用。' if subject == '课程查询' else
                '单级请假类型已启用，可以完善建设需求并选择请假模板；发布后由已认证发布管理员审批。' if leave_available else
                f'当前没有可用的“{subject}”应用类型。可以先保存建设需求，接入对应能力后再创建应用；保存需求不会发布应用。',
            candidates=[candidate for candidate in choices if any(app['id'] == candidate.id and app.get('type_id', 'campus.course-query') == ('platform.form' if subject == '简单登记' else 'campus.course-query') for app in applications)] if subject in ('课程查询', '简单登记') else [])
    # A generic question can itself be an application name. Do not mistake it for an explicit switch.
    if not semantic_matches and resolve_course(text, campus_today()).status == 'resolved':
        matches = []
    intent_text = text
    for app in matches:
        intent_text = strip_names(intent_text, app)
    if not discovery and re.search(r'不要|别|不想|无需|取消', intent_text):
        return ApplicationResolution(status='unsupported', message='未切换应用或执行操作，请明确你希望使用的应用与功能。')
    management = not discovery and bool(re.search(r'配置|设置|管理|发布|删除|停用|归档|修改|编辑|问法|规则|工作台|新建', intent_text))
    if management and not admin:
        return ApplicationResolution(status='unsupported', message='应用配置需要管理员权限；你可以使用已授权应用的查询功能。')
    if text in {'应用工作台', '打开应用工作台', '进入应用工作台', '管理应用', '新建应用', '应用配置'}:
        return ApplicationResolution(status='navigation', target='admin', message='可进入应用工作台管理应用；保存与发布请在配置页核对确认。')
    if not applications:
        return ApplicationResolution(status='clarification', message='当前没有匹配且可访问的应用，请换个关键词或联系管理员确认访问范围。')
    if len(matches) > 1 and not command.selected_application_id:
        return ApplicationResolution(status='clarification', message='匹配到多个应用，请先选择一个。',
            candidates=[candidate for candidate in choices if candidate.id in {app['id'] for app in matches}])
    selected = None
    if command.selected_application_id:
        allowed = matches or applications
        selected = next((a for a in allowed if a['id'] == command.selected_application_id), None)
        if selected is None:
            return ApplicationResolution(status='clarification', message='所选应用不可用或无访问权限，请重新选择。', candidates=choices)
    elif len(matches) == 1:
        selected = matches[0]
    # Unknown explicit application references must not silently run in the previous application.
    reference_text = re.sub(r'这个应用|当前应用|这个助手|当前助手', '', text)
    if not selected and re.search(r'打开|进入|(?:用|使用|通过|在).+(?:应用|助手|服务)|切换到|切到|搜索|查找|(?:配置|设置|管理|修改).+应用', reference_text):
        return ApplicationResolution(status='clarification', message='没有找到可访问的匹配应用，请从下方选择。', candidates=choices)
    if not selected and command.current_application_id:
        selected = next((a for a in applications if a['id'] == command.current_application_id), None)
        if selected is None:
            return ApplicationResolution(status='clarification', message='当前应用已不可用或权限发生变化，请重新选择。', candidates=choices)
    if not selected:
        # No default schedule app. A generic capability request must first disambiguate instances.
        if re.search(r'课表|课程|上课|今天|明天|后天|本周|下周|今日|明日|星期|礼拜', text):
            eligible = [a for a in applications if a.get('type_id', 'campus.course-query') == 'campus.course-query']
        elif re.search(r'登记|填表|表单', text):
            eligible = [a for a in applications if a.get('type_id') == 'platform.form']
        elif management or command.selected_application_id:
            eligible = applications
        else:
            return ApplicationResolution(status='clarification', message='先告诉我应用名称，或从下方选择应用，再描述要使用的功能。', candidates=choices)
        if len(eligible) != 1:
            return ApplicationResolution(status='clarification', message='请先确定使用哪个应用，再匹配应用功能。',
                candidates=[candidate for candidate in choices if candidate.id in {a['id'] for a in eligible}])
        selected = eligible[0]
    residual = text
    # Strip only the explicit name and adjacent quotation delimiters. Preserve instruction/date safety words.
    if matches:
        residual = strip_names(text, selected).strip('：:，, ')
        residual = re.sub(r'^(?:请|帮我|我想|我要|打开|进入|使用|切换到|切到|切换|用|通过|在)+', '', residual).lstrip('：:，, ')
        residual = re.sub(r'^(?:里面|里|中)(?=查|看|今天|明天|后天|本周|下周)', '', residual)
    if selected.get('type_id') in ('platform.form', 'platform.leave-approval'):
        is_approval = selected['type_id'] == 'platform.leave-approval'
        base = {'application_id': selected['id'], 'application_name': selected['name'], 'type_id': selected['type_id']}
        if management:
            return ApplicationResolution(status='navigation', target='admin', tab='questions' if re.search(r'记录|数据', residual) else 'page' if re.search(r'字段|表单|页面', residual) else 'basic',
                message=f"已定位“{selected['name']}”的表单管理入口。", **base)
        if selected.get('usable') is False:
            return ApplicationResolution(status='navigation', target='admin', message='应用尚未开放给此身份，请先完成配置和访问范围核对。', **base)
        if re.search(r'课表|课程|采购|发邮件|运行命令', residual) or not is_approval and re.search(r'审批', residual):
            return ApplicationResolution(status='unsupported', message='当前应用是登记表单，请先切换到对应业务应用。', **base)
        if is_approval:
            actions = [FeatureAction(id='leave-fill', label='填写请假申请', text='填写请假申请'), FeatureAction(id='leave-progress', label='我的申请进度', text='查看我的申请进度')]
            if admin:
                actions.append(FeatureAction(id='leave-work', label='审批待办', text='查看审批待办'))
            return ApplicationResolution(status='selected', message=f"已打开“{selected['name']}”。可提交请假、查看进度，待审时可撤回；审批需有效管理员身份。",
                runtime_action='records' if re.search(r'进度|记录|待办|处理|通过|拒绝|撤回', residual) else 'fill',
                features=['填写申请', '查看本人进度', '待审撤回'] + (['查看审批待办'] if admin else []), feature_actions=actions, **base)
        return ApplicationResolution(status='selected', message=f"已打开“{selected['name']}”，可填写登记或查看本人记录。",
            runtime_action='records' if re.search(r'记录|已提交|结果', residual) else 'fill', features=['填写登记', '查看本人记录'],
            feature_actions=[FeatureAction(id='form-fill', label='填写登记', text='填写登记表'), FeatureAction(id='form-records', label='我的登记记录', text='查看我的登记记录')], **base)
    features = ['查询本人课表', '按日期查询', '查看应用功能']
    actions = [FeatureAction(id='query-today', label='查询今天课表', text='今天课表'),
        FeatureAction(id='query-week', label='查询本周课表', text='本周课表'),
        FeatureAction(id='show-features', label='查看应用功能', text='这个应用有哪些功能')]
    if admin:
        features += ['配置基础信息', '配置展示字段', '管理问法与规则', '查看发布记录']
        actions += [FeatureAction(id='configure-basic', label='基础信息', text='配置基础信息'),
            FeatureAction(id='configure-page', label='展示字段', text='配置页面字段'),
            FeatureAction(id='configure-questions', label='问法与规则', text='管理问法与规则'),
            FeatureAction(id='view-releases', label='发布记录', text='查看发布记录')]
    result = {'application_id': selected['id'], 'application_name': selected['name'], 'features': features, 'feature_actions': actions}
    if management:
        if re.search(r'数据管理|管理.*数据|维护.*数据|导出', residual):
            return ApplicationResolution(status='unsupported', message='当前课程应用提供本人课表查询，业务数据维护入口尚未接入。', **result)
        tab = 'questions' if re.search(r'问法|规则', residual) else 'page' if re.search(r'字段|展示|页面', residual) else 'history' if re.search(r'发布记录|历史|审计', residual) else 'basic'
        return ApplicationResolution(status='navigation', target='admin', tab=tab,
            message=f"已定位“{selected['name']}”的管理入口，请进入配置页操作并核对变更。", **result)
    if selected.get('usable') is False:
        return ApplicationResolution(status='navigation', target='admin', tab='basic',
            message=f"“{selected['name']}”当前尚未开放给此身份使用，请先查看配置和访问范围。", **result)
    if re.search(r'请假|采购|预约|审批|提交|删除|发邮件|执行|运行命令|天气|新闻|成绩|库存', residual):
        return ApplicationResolution(status='unsupported', message='当前应用只支持课程查询，该操作尚未接入。', **result)
    app_only = bool(matches) and bool(re.fullmatch(r'(?:请|帮我|我想|我要|打开|进入|使用|切换到|切到|切换|应用|服务|微服务|的|吧|一下|看看|用|在|到|[：:，, ])*', residual))
    if discovery or app_only or len(matches) > 1 or re.search(r'功能|能做什么|能干什么|怎么用|帮助', residual):
        return ApplicationResolution(status='selected', message=f"已锁定“{selected['name']}”，你可以查询今天、明天或本周的课表。", **result)
    relative = re.fullmatch(r'(?:查|查询|看|看看)?(今天|明天|后天|今日|明日|后日|本周|下周|这周|[本这下](?:周|星期|礼拜)[一二三四五六日天])(?:呢|的呢)?', residual)
    if relative:
        residual = relative[1] + '课表'
    return ApplicationResolution(status='query', query_text=residual, message=f"使用“{selected['name']}”匹配查询功能。", **result)


@router.post('/resolve', response_model=ApplicationResolution)
def resolve_request(command: ResolveApplication, request: Request):
    principal = current_user(request)
    from app.modules.config.applications import catalog
    normalized = key(command.text)
    searching = re.match(r'(?:请|帮我)?(?:搜索|查找|寻找)', normalized)
    management = not searching and bool(re.search(r'配置|设置|管理|发布|删除|停用|归档|修改|编辑|问法|规则|工作台|新建', normalized))
    is_admin = 'config_admin' in principal.roles
    if management and is_admin:
        from app.modules.config.routes import admin as authorize_admin, execute
        from app.modules.config.application_store import ApplicationStore
        authorize_admin(request)
        usable = {row['id']: row for row in catalog(request)['applications'] if row['ready']}
        rows = execute(lambda store: ApplicationStore(store).listing()['items'], principal)
        applications = [{'id': 'schedule' if row['legacy'] else row['application_id'],
            'name': row['name'], 'type_id': row['type_id'],
            'aliases': [usable.get('schedule' if row['legacy'] else row['application_id'], {}).get('name', row['name'])],
            'usable': ('schedule' if row['legacy'] else row['application_id']) in usable} for row in rows]
    else:
        rows = catalog(request)['applications']
        applications = [{'id': row['id'], 'name': row['name'], 'type_id': row.get('type_id', 'campus.course-query'),
            'category': row['category'], 'description': row['desc']} for row in rows if row['ready']]
    return resolve_application(command, applications, is_admin)
