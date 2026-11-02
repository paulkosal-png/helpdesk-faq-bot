output "image" {
  description = "Push your image here (see README > Deploy)"
  value       = local.image
}

output "service_account_email" {
  description = "Share your FAQ Google Sheet with this email as Editor"
  value       = google_service_account.bot.email
}

output "service_name" {
  value = google_cloud_run_v2_service.bot.name
}
