# Data sources for Key Vault secrets
# Naming convention: platform-<platform>-<purpose>

data "azurerm_key_vault_secret" "auth_password" {
  name         = "platform-azureagentforge-auth-password"
  key_vault_id = module.keyvault.id
}

data "azurerm_key_vault_secret" "gateway_token" {
  name         = "platform-azureagentforge-gateway-token"
  key_vault_id = module.keyvault.id
}

data "azurerm_key_vault_secret" "telegram" {
  count        = var.telegram_enabled ? 1 : 0
  name         = "platform-telegram-bot-token"
  key_vault_id = module.keyvault.id
}

data "azurerm_key_vault_secret" "cf_tunnel_token" {
  name         = "platform-cloudflared-token"
  key_vault_id = module.keyvault.id
}
