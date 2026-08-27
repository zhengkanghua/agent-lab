import type { components } from './generated/openapi'
import { ApiError, requestJson } from './client'
import { hasText, isNonNegativeInteger, isRecord, isUuid } from './json-guards'

export type UserAdminDto = components['schemas']['UserAdminResponse']
export type UserAdminCreateRequest = components['schemas']['UserAdminCreateRequest']
export type UserAdminUpdateRequest = components['schemas']['UserAdminUpdateRequest']
export type UserAdminPasswordRequest = components['schemas']['UserAdminPasswordRequest']
export type UserSessionRevocationDto = components['schemas']['UserSessionRevocationResponse']

export interface CreateUserOptions {
  email: string
  password: string
  isSuperuser: boolean
}

export interface UpdateUserOptions {
  userId: string
  isActive?: boolean
  isSuperuser?: boolean
}

export interface ResetUserPasswordOptions {
  userId: string
  password: string
}

export async function listUsers(signal?: AbortSignal): Promise<UserAdminDto[]> {
  const response = await requestJson<unknown>('/admin/users', { method: 'GET', signal })
  if (!Array.isArray(response) || !response.every(isUserAdminDto)) {
    throw invalidAdminResponse('The account service returned an invalid user list.')
  }
  return response
}

export async function createUser({
  email,
  password,
  isSuperuser,
}: CreateUserOptions): Promise<UserAdminDto> {
  const payload: UserAdminCreateRequest = {
    email,
    password,
    is_superuser: isSuperuser,
  }
  return requestUser('/admin/users', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateUser({
  userId,
  isActive,
  isSuperuser,
}: UpdateUserOptions): Promise<UserAdminDto> {
  const payload: UserAdminUpdateRequest = {
    ...(isActive === undefined ? {} : { is_active: isActive }),
    ...(isSuperuser === undefined ? {} : { is_superuser: isSuperuser }),
  }
  return requestUser(`/admin/users/${encodeURIComponent(userId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function resetUserPassword({
  userId,
  password,
}: ResetUserPasswordOptions): Promise<UserAdminDto> {
  const payload: UserAdminPasswordRequest = { password }
  return requestUser(`/admin/users/${encodeURIComponent(userId)}/password`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function revokeUserSessions(userId: string): Promise<UserSessionRevocationDto> {
  const response = await requestJson<unknown>(
    `/admin/users/${encodeURIComponent(userId)}/sessions`,
    { method: 'DELETE' },
  )
  if (!isRecord(response) || !isNonNegativeInteger(response.revoked_sessions)) {
    throw invalidAdminResponse('The account service returned an invalid revocation result.')
  }
  return response as unknown as UserSessionRevocationDto
}

async function requestUser(path: string, init: RequestInit): Promise<UserAdminDto> {
  const response = await requestJson<unknown>(path, init)
  if (!isUserAdminDto(response)) {
    throw invalidAdminResponse('The account service returned an invalid user.')
  }
  return response
}

function isUserAdminDto(value: unknown): value is UserAdminDto {
  return (
    isRecord(value) &&
    isUuid(value.id) &&
    hasText(value.email) &&
    typeof value.is_active === 'boolean' &&
    typeof value.is_superuser === 'boolean' &&
    typeof value.is_verified === 'boolean' &&
    typeof value.is_environment_admin === 'boolean' &&
    isIsoDateTime(value.created_at) &&
    isIsoDateTime(value.updated_at)
  )
}

function isIsoDateTime(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0 && !Number.isNaN(Date.parse(value))
}

function invalidAdminResponse(message: string): ApiError {
  return new ApiError({ message, code: 'response_invalid' })
}
