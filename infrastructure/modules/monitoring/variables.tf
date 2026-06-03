variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "project" { type = string }
variable "environment" { type = string }

variable "tags" {
  type    = map(string)
  default = {}
}

variable "log_retention_in_days" {
  description = "Log Analytics workspace retention in days (cost-optimized default: 30)."
  type        = number
  default     = 30
}

variable "log_daily_quota_gb" {
  description = "Log Analytics daily ingestion cap in GB (-1 = unlimited; cost-optimized default: 1)."
  type        = number
  default     = 1
}
