terraform {
  backend "s3" {
    bucket         = "forex-terraform-state-bucket-163859990434-ap-southeast-2-an"
    key            = "dev/terraform.tfstate"
    region         = "ap-southeast-2"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}