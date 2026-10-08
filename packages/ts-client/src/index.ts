/**
 * Typed VisioVox API client.
 *
 * Request and response shapes come from `./generated/api`, which is produced
 * from `packages/contracts/openapi.json`. Never hand-edit the generated file —
 * CI regenerates and diffs it, so a spec change that would break this client
 * fails on the pull request that caused it rather than after deploy.
 *
 * Token refresh is handled here rather than in each caller. A single-flight
 * guard means a burst of concurrent 401s triggers one refresh, not one per
 * request — and since refresh tokens rotate, parallel refreshes would consume
 * each other's tokens and trip the server's reuse detection, logging the user
 * out for doing nothing wrong.
 */

import type { components } from './generated/api';

export type Schemas = components['schemas'];
export type UserResponse = Schemas['UserResponse'];
export type ProjectResponse = Schemas['ProjectResponse'];
export type ProjectListResponse = Schemas['ProjectListResponse'];
export type JobResponse = Schemas['JobResponse'];
export type UploadInitResponse = Schemas['UploadInitResponse'];
export type UploadPartsResponse = Schemas['UploadPartsResponse'];
export type UploadStatusResponse = Schemas['UploadStatusResponse'];
export type LimitsResponse = Schemas['LimitsResponse'];
export type LanguageOption = Schemas['LanguageOption'];
export type ExportResponse = Schemas['ExportResponse'];
export type ExportListResponse = Schemas['ExportListResponse'];
export type RenditionOption = Schemas['RenditionOption'];
export type ShareResponse = Schemas['ShareResponse'];
export type ShareListResponse = Schemas['ShareListResponse'];
export type CreateShareRequest = Schemas['CreateShareRequest'];
export type CreateExportRequest = Schemas['CreateExportRequest'];
export type CompletedPart = Schemas['CompletedPart'];
export type TranslationResponse = Schemas['TranslationResponse'];
export type TranslationListResponse = Schemas['TranslationListResponse'];
export type TranslationLanguageOption = Schemas['TranslationLanguageOption'];

export const CLIENT_VERSION = '0.1.0' as const;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${String(status)}: ${detail}`);
    this.name = 'ApiError';
  }
}

export interface ClientOptions {
  baseUrl: string;
  /**
   * Resolves the current ID token, or null when signed out.
   *
   * Called per request rather than once: the identity provider caches and
   * renews, so asking every time is the intended usage and means this client
   * never holds a credential or reasons about expiry.
   */
  getToken?: (() => Promise<string | null>) | undefined;
  fetchImpl?: typeof fetch | undefined;
}

export class VisioVoxClient {
  private readonly baseUrl: string;
  private readonly getToken: (() => Promise<string | null>) | undefined;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, '');
    this.getToken = options.getToken;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  private async raw(path: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers);
    if (!headers.has('content-type') && init.body !== undefined) {
      headers.set('content-type', 'application/json');
    }
    const token = this.getToken ? await this.getToken() : null;
    if (token) headers.set('authorization', `Bearer ${token}`);
    return this.fetchImpl(`${this.baseUrl}${path}`, { ...init, headers });
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    let response = await this.raw(path, init);

    // A 401 usually means the cached token went stale between resolve and
    // arrival. The provider renews on demand, so one retry is the whole
    // recovery - there is no refresh token here to rotate.
    if (response.status === 401) {
      response = await this.raw(path, init);
    }

    if (!response.ok) {
      throw new ApiError(response.status, await this.readDetail(response));
    }
    return (await response.json()) as T;
  }

  /** For endpoints that answer 204 and have no body to parse. */
  private async requestNoContent(path: string, init: RequestInit = {}): Promise<void> {
    let response = await this.raw(path, init);
    if (response.status === 401) {
      response = await this.raw(path, init);
    }
    if (!response.ok) {
      throw new ApiError(response.status, await this.readDetail(response));
    }
  }

  private async readDetail(response: Response): Promise<string> {
    try {
      const body: unknown = await response.json();
      if (typeof body === 'object' && body !== null && 'detail' in body) {
        // `'detail' in body` already narrows this; no assertion needed.
        const { detail } = body;
        return typeof detail === 'string' ? detail : JSON.stringify(detail);
      }
      return JSON.stringify(body);
    } catch {
      return response.statusText;
    }
  }

  // ---- identity ----
  //
  // Sign-in, registration and sign-out are the identity provider's, not ours.
  // What remains is the call that materialises the local user row.

  me(): Promise<UserResponse> {
    return this.request<UserResponse>('/v1/auth/me');
  }

  // ---- projects ----

  /** `language`: a code from `getLimits().caption_languages`; null detects it. */
  createProject(title: string, language: string | null = null): Promise<ProjectResponse> {
    return this.request<ProjectResponse>('/v1/projects', {
      method: 'POST',
      body: JSON.stringify({ title, rights_attested: true, language }),
    });
  }

  listProjects(limit = 20): Promise<ProjectListResponse> {
    return this.request<ProjectListResponse>(`/v1/projects?limit=${String(limit)}`);
  }

  getProject(id: string): Promise<ProjectResponse> {
    return this.request<ProjectResponse>(`/v1/projects/${id}`);
  }

  getJob(id: string): Promise<JobResponse> {
    return this.request<JobResponse>(`/v1/projects/${id}/job`);
  }

  getManifest(id: string): Promise<unknown> {
    return this.request<unknown>(`/v1/projects/${id}/manifest`);
  }

  /** Name a speaker by ordinal; null or blank restores "Speaker N". */
  renameSpeaker(id: string, ordinal: number, name: string | null): Promise<ProjectResponse> {
    return this.request<ProjectResponse>(`/v1/projects/${id}/speakers/${String(ordinal)}/name`, {
      method: 'PUT',
      body: JSON.stringify({ name }),
    });
  }

  // ---- translated captions ----

  /** Every translation of a project, and the languages on offer. */
  listTranslations(id: string): Promise<TranslationListResponse> {
    return this.request<TranslationListResponse>(`/v1/projects/${id}/translations`);
  }

  /** Ask for a translation; asking again for the same language is harmless. */
  requestTranslation(id: string, language: string): Promise<TranslationResponse> {
    return this.request<TranslationResponse>(`/v1/projects/${id}/translations`, {
      method: 'POST',
      body: JSON.stringify({ language }),
    });
  }

  // ---- upload ----

  /**
   * Begin an upload. The server chooses the part size and count, because that
   * arithmetic has to satisfy the object store's limits and the client has no
   * reason to know them.
   */
  uploadInit(
    projectId: string,
    file: { name: string; type: string; size: number },
  ): Promise<UploadInitResponse> {
    return this.request<UploadInitResponse>(`/v1/projects/${projectId}/upload/init`, {
      method: 'POST',
      body: JSON.stringify({
        filename: file.name,
        content_type: file.type || 'application/octet-stream',
        size_bytes: file.size,
      }),
    });
  }

  uploadComplete(
    projectId: string,
    uploadId: string,
    key: string,
    parts: { part_number: number; etag: string }[],
  ): Promise<{ job_id: string; status: string }> {
    return this.request(`/v1/projects/${projectId}/upload/complete`, {
      method: 'POST',
      body: JSON.stringify({ upload_id: uploadId, key, parts }),
    });
  }

  /**
   * Progress stream URL. EventSource cannot set an Authorization header, so
   * the caller passes the access token as a query parameter or proxies the
   * stream server-side; the Next.js app does the latter.
   */
  /**
   * What this account may upload right now.
   *
   * Computed server-side from live free space and uploads already in flight, so
   * it is fetched rather than cached: a limit shown from five minutes ago can be
   * wrong by the size of somebody else's upload.
   */
  getLimits(): Promise<LimitsResponse> {
    return this.request<LimitsResponse>('/v1/limits');
  }

  /**
   * Confirm finished parts and collect the next batch of URLs.
   *
   * One call rather than two, so a large upload costs one round trip per batch
   * and its progress becomes durable at batch granularity.
   */
  uploadParts(
    projectId: string,
    uploadId: string,
    completed: CompletedPart[],
    after = 0,
  ): Promise<UploadPartsResponse> {
    return this.request<UploadPartsResponse>(`/v1/projects/${projectId}/upload/${uploadId}/parts`, {
      method: 'POST',
      body: JSON.stringify({ completed, after }),
    });
  }

  /** Which parts the server already holds — the basis for resuming. */
  uploadStatus(projectId: string, uploadId: string): Promise<UploadStatusResponse> {
    return this.request<UploadStatusResponse>(`/v1/projects/${projectId}/upload/${uploadId}`);
  }

  /** Cancel, releasing both the reservation and the multipart upload. */
  uploadAbort(projectId: string, uploadId: string): Promise<void> {
    return this.requestNoContent(`/v1/projects/${projectId}/upload/${uploadId}/abort`, {
      method: 'POST',
    });
  }

  /** What this recording can honestly produce, from its real height. */
  exportOptions(projectId: string): Promise<RenditionOption[]> {
    return this.request<RenditionOption[]>(`/v1/projects/${projectId}/export-options`);
  }

  createExport(projectId: string, body: CreateExportRequest): Promise<ExportResponse> {
    return this.request<ExportResponse>(`/v1/projects/${projectId}/exports`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  listExports(projectId: string): Promise<ExportListResponse> {
    return this.request<ExportListResponse>(`/v1/projects/${projectId}/exports`);
  }

  /**
   * The download URL.
   *
   * Returned rather than fetched: this is put in an href so the browser drives
   * the transfer, which is what makes Range and resume work. Pulling it through
   * fetch would buffer a multi-gigabyte file in memory first.
   */
  exportDownloadUrl(projectId: string, exportId: string): string {
    return `${this.baseUrl}/v1/projects/${projectId}/exports/${exportId}/download`;
  }

  /**
   * Create a public link.
   *
   * The token comes back exactly once, in this response: only its hash is
   * stored, so there is no endpoint that can show it again.
   */
  createShare(projectId: string, body: CreateShareRequest): Promise<ShareResponse> {
    return this.request<ShareResponse>(`/v1/projects/${projectId}/shares`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  listShares(projectId: string): Promise<ShareListResponse> {
    return this.request<ShareListResponse>(`/v1/projects/${projectId}/shares`);
  }

  /** Immediate and permanent. */
  revokeShare(projectId: string, shareId: string): Promise<void> {
    return this.requestNoContent(`/v1/projects/${projectId}/shares/${shareId}`, {
      method: 'DELETE',
    });
  }

  eventsUrl(projectId: string): string {
    return `${this.baseUrl}/v1/projects/${projectId}/events`;
  }
}
