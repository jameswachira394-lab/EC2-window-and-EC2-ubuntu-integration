output "vpc_id" {
  value = aws_vpc.main.id
}

output "ubuntu_public_ip" {
  value = aws_instance.ubuntu.public_ip
}

output "windows_public_ip" {
  value = aws_instance.windows.public_ip
}

output "cloudwatch_log_group" {
  value = aws_cloudwatch_log_group.logs.name
}