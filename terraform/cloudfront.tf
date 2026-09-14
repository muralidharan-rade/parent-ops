# ---------------------------------------------------------
# CloudFront Distribution in front of the Ingestion ALB
# Provides HTTPS without needing a custom domain.
# Telegram requires HTTPS for webhook URLs.
# ---------------------------------------------------------

resource "aws_cloudfront_distribution" "ingestion_cdn" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "Parent Copilot - HTTPS proxy for Ingestion ALB"

  origin {
    domain_name = aws_lb.ingestion_alb.dns_name
    origin_id   = "ingestion-alb-origin"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only" # ALB is HTTP, CloudFront handles HTTPS
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "ingestion-alb-origin"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    # Disable caching - we want all webhook calls to pass through immediately
    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0

    forwarded_values {
      query_string = true
      headers      = ["*"] # Forward all headers to the origin

      cookies {
        forward = "all"
      }
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true # Use the free *.cloudfront.net HTTPS cert
  }

  depends_on = [aws_lb_listener.ingestion_listener]
}

output "ingestion_webhook_url" {
  value       = "https://${aws_cloudfront_distribution.ingestion_cdn.domain_name}"
  description = "CloudFront HTTPS URL for the Telegram Webhook"
}
