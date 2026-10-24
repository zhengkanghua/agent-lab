export { default as SettingsNav, type SettingsSection } from './components/SettingsNav.vue'
export { default as AccountSection } from './components/AccountSection.vue'
export { default as SearchPreferencesSection } from './components/SearchPreferencesSection.vue'
export { default as UsageSection } from './components/UsageSection.vue'
export { usePreferences } from './composables/usePreferences'
export {
  DEFAULT_PREFERENCES,
  fromRemotePreferences,
  sanitizePreferences,
  toRemotePreferences,
  type UserPreferences,
} from './model/preferences'
