terraform {
  backend "azurerm" {
    resource_group_name  = "rg-terraform-state"
    storage_account_name = "YOUR_TF_STATE_STORAGE_ACCOUNT"
    container_name       = "tfstate"
    key                  = "dev.terraform.tfstate"
    subscription_id      = "00000000-0000-0000-0000-000000000000"
    tenant_id            = "00000000-0000-0000-0000-000000000000"
  }
}
