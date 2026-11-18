export type Session = {access_token:string; user:{email:string; role:string}};

export async function request(path:string, token = '', body?:unknown) {
  const headers = new Headers();
  if (token) headers.set('Authorization', 'Bearer ' + token);
  const upload = body instanceof FormData;
  if (body !== undefined && !upload) headers.set('Content-Type','application/json');
  const response = await fetch('/api/v1' + path, {headers, method: body === undefined ? 'GET':'POST',
    body: upload ? body : JSON.stringify(body)});
  if (response.status === 401 && token) window.dispatchEvent(new Event('session-expired'));
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message || response.statusText);
  return result;
}
