export const userAdminKeys = {
  all: ['user-admin'] as const,
  users: () => [...userAdminKeys.all, 'users'] as const,
}
