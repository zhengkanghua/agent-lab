export { default as SettingsNav, type SettingsSection } from './components/SettingsNav.vue'
export { default as AccountSection } from './components/AccountSection.vue'
export { default as SearchPreferencesSection } from './components/SearchPreferencesSection.vue'
export { default as AgentPromptSection } from './components/AgentPromptSection.vue'
export { usePreferences } from './composables/usePreferences'
export {
  DEFAULT_PREFERENCES,
  PREFERENCES_STORAGE_KEY,
  sanitizePreferences,
  validateAgentSystemPrompt,
  type UserPreferences,
} from './model/preferences'
