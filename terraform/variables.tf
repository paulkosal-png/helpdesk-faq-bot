variable "project_id" {
  description = "GCP project ID (use a personal project, not an employer's)"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-west1"
}

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
  default     = "helpdesk-faq-bot"
}

variable "image_tag" {
  description = "Container image tag pushed to Artifact Registry"
  type        = string
  default     = "latest"
}

variable "google_sheet_id" {
  description = "Google Sheet ID holding the FAQ (share it with the bot's service account email, see outputs). Empty = use bundled CSV."
  type        = string
  default     = ""
}

variable "llm_base_url" {
  description = "OpenAI-compatible base URL for the RAG fallback. Empty = no LLM (unmatched questions escalate)."
  type        = string
  default     = ""
}

variable "llm_model" {
  description = "LLM model name, e.g. gpt-4o-mini"
  type        = string
  default     = ""
}
