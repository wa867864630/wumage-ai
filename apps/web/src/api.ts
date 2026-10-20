export class SessionExpired extends Error {}
export class HTTPError extends Error {constructor(message: string, readonly status: number) {super(message);}}
// Preserve user-authored JSON for server-side duplicate-key and strict parsing checks.
export class JSONRequest {constructor(readonly json: string) {}}
export async function api<T>(path: string, token = '', body?: unknown, signal?: AbortSignal, method?: string): Promise<T> {
  let response: Response;
  try {response = await fetch(path, {method: method ?? (body === undefined ? 'GET' : 'POST'),
    headers: {...(token ? {Authorization: `Bearer ${token}`} : {}), ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
    ...(body === undefined ? {} : {body: body instanceof JSONRequest ? body.json : JSON.stringify(body)}), signal: signal ?? AbortSignal.timeout(12000)});}
  catch (cause) {
    if (signal?.aborted || (cause instanceof Error && ['AbortError', 'TimeoutError'].includes(cause.name))) {
      throw new Error('请求已中断或超时，请检查服务状态后重试。');
    }
    throw new Error('无法连接服务，请检查网络和后端是否启动后重试。');
  }
  if (response.status === 401) throw new SessionExpired('登录已失效，请重新登录。');
  if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('服务暂不可用，请检查 API 是否启动。');
  let data;
  try {data = await response.json();}
  catch {throw new Error('服务返回了不完整的响应，请稍后重试。');}
  if (!response.ok) {
    const details = Array.isArray(data?.error?.details) ? data.error.details
      .filter((item: unknown): item is {path: string; message: string} => !!item && typeof item === 'object' && 'path' in item && 'message' in item && typeof item.path === 'string' && typeof item.message === 'string')
      .map((item: {path: string; message: string}) => `${item.path}: ${item.message}`) : [];
    const trace = typeof data?.trace_id === 'string' ? data.trace_id : response.headers.get('X-Trace-ID');
    throw new HTTPError([typeof data?.error?.message === 'string' ? data.error.message : '请求失败，请稍后重试。',
      ...details, ...(trace ? [`请求编号 ${trace}`] : [])].join(' '), response.status);
  }
  return data as T;
}
export type Identity = {subject: string; tenant_id: string; roles: string[]; student_id: string | null; mode: string};
export type Session = {token: string; name: string; identity: Identity};
