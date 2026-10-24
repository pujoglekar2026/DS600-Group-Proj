# Nimbus RM on the AWS Academy sandbox with CloudFront

This deployment uses CloudFormation to create the application network, EC2
server, private RDS database, and CloudFront distribution. It targets the
AWS Academy sandbox described in
[`AWS_ACADEMY_SANDBOX_LIMITATIONS.txt`](AWS_ACADEMY_SANDBOX_LIMITATIONS.txt) and
must be deployed in **us-east-1**.

## Architecture

```text
Browser -- HTTPS --> CloudFront -- HTTP + origin token --> EC2 (Nginx/Gunicorn/Flask)
                                                         |
                                                         +--> private RDS MySQL
```

CloudFront provides HTTPS at its generated `*.cloudfront.net` hostname, so the
deployment does not need a registered domain, Route 53, or a customer-managed
TLS certificate. The Flask application and database remain the existing app;
CloudFront is the public entry point and EC2 is its origin.

## Deploy

1. Start the lab and check that the Region is **us-east-1**. Push the application
   to a public GitHub repository so the EC2 bootstrap script can clone it.
2. Generate three independent random values locally (do not commit the values):

   ```bash
   python3 -c "import secrets; print('AppSecretKey=' + secrets.token_hex(32)); print('OriginVerifyToken=' + secrets.token_hex(32)); print('DBPassword=' + secrets.token_hex(20))"
   ```

   Enter each value in its matching parameter. The 40-character database
   password stays within RDS's 41-character maximum and uses allowed characters.
3. In CloudFormation, create a stack using
   `infra/nimbus-merged-stack.yaml`.
4. Provide the public repository URL, branch, generated secrets, and an allowed
   instance size. Keep the `t3.micro` defaults unless the lab requires otherwise.
   Set `SSHAllowedCidr` to your own public IP with `/32` if SSH is needed;
   otherwise keep its default and connect through Systems Manager Session
   Manager using the pre-created `LabInstanceProfile`.
5. Wait for the stack to complete and the CloudFront distribution status to
   become **Deployed**. Open the `ApplicationURL` output. The first boot installs
   Python packages and may take several minutes.
6. Create a demo login with Session Manager:

   ```bash
   cd /opt/nimbus
   sudo -u nimbus .venv/bin/flask --app wsgi seed-demo
   ```

   Then log in to the CloudFront URL with `demo / demo12345`.

## Why these resources and settings

| Resource or setting | Reason |
|---|---|
| CloudFormation | Reproducible setup and teardown of the resources the sandbox permits; fewer manual configuration errors. |
| CloudFront distribution | Required public edge endpoint; gives browser-to-app HTTPS using the default CloudFront certificate without a custom domain or ACM certificate. |
| CloudFront caching disabled | This app serves personalized pages and session cookies and uses query parameters for filtering. Forwarding cookies and query strings while disabling caching avoids serving one user's content to another or returning stale pages. Static-file caching can be added later as a separate behavior with deliberate cache invalidation. |
| CloudFront origin verification header | Nginx rejects direct requests to the public EC2 origin unless they carry the secret header CloudFront adds. This mitigates bypassing the distribution, although it is not equivalent to a private origin. |
| EC2 in a public subnet | The app needs outbound internet access during setup to clone the public repository and install dependencies. A public subnet plus Internet Gateway avoids a NAT gateway and its cost. |
| Existing `LabInstanceProfile` | IAM is read-only in the sandbox, so the template does not attempt to create a role. The pre-created profile supports Session Manager access. |
| RDS MySQL in private subnets | Persists app users, clients, and discussion snapshots without a public database endpoint. Its security group permits MySQL only from the EC2 security group. |
| Two private subnets, single-AZ RDS | RDS subnet groups require subnets in at least two Availability Zones. The database itself remains single-AZ because the lab disallows Multi-AZ deployments. |
| Small burstable instances and gp2 | These stay within the sandbox's EC2, RDS, EBS type, and storage limits; `t3.micro` and 20 GiB gp2 are the starting defaults. |
| CloudFront `PriceClass_100` | Reduces distribution cost by using the lower-cost edge locations. Requests from Asia can have higher latency than with `PriceClass_All`. |

The app is server-rendered Flask, so S3 static-site hosting alone cannot serve
its authenticated pages or API. A static-site bucket would add deployment and
asset synchronization work without removing the need for the EC2 application
server. The stack does create a separate private S3 bucket solely for old
discussion-snapshot archives.
A load balancer and Auto Scaling group are also omitted: there is one small
origin in this temporary lab, and adding another compute tier would increase
cost and setup complexity without providing meaningful availability.

## Considerations and limitations

- **Region and service quotas:** Deploy in `us-east-1`. The stack creates an EC2
  instance and RDS database, both consuming lab resources. The template avoids
  creating IAM roles and does not use a NAT gateway, load balancer, Elastic IP,
  or Multi-AZ database.
- **Session cleanup and data loss:** The sandbox resets resources when its timer
  expires. RDS uses `DeletionPolicy: Delete`, so deleting the stack also deletes
  the database. The snapshot archive bucket is retained when the stack is
  deleted; remove its contents and bucket manually when they are no longer
  needed. Export any required project data before ending the lab; this is not a
  durable production hosting arrangement.
- **Snapshot archive:** A monthly EC2 systemd timer moves snapshots whose
  `discussed_on` date is more than five calendar years old from RDS to private,
  SSE-S3-encrypted S3 objects. The job verifies each object before deleting the
  database row. S3 transitions the archive objects to Glacier Flexible
  Retrieval after 30 days. Archived snapshots are not available in the app;
  retrieval requires restoring the object from Glacier. The archive contains
  client names and discussion notes, so protect access to the bucket. To run
  the archive immediately, use Session Manager and run
  `sudo -u nimbus /opt/nimbus/.venv/bin/flask --app wsgi archive-snapshots`.
  The lifecycle rule does not expire archive objects, so they remain in Glacier
  and continue to incur storage charges until manually deleted.
  This is based on discussion date, not last access, and five years is a
  project default rather than a compliance recommendation. Archiving reduces
  live database rows and future growth; it does not shrink the provisioned
  20-GiB RDS storage allocation or immediately reduce its storage charge.
- **Archive IAM permissions:** IAM is read-only in the lab, so the stack cannot
  create a scoped policy. The archiver uses the existing `LabInstanceProfile`.
  Confirm that it permits `s3:PutObject` and `s3:GetObject` for the archive
  bucket; otherwise the job will fail without deleting the RDS row.
- **Origin networking:** EC2 must accept port 80 because CloudFront connects to
  it over HTTP. Nginx validates the CloudFront-only secret header, but the EC2
  origin still has a public address and the header can be read by principals
  with permission to inspect the distribution or instance bootstrap data.
  Restrict SSH to a single admin IP or use Session Manager. Do not reuse the
  origin token or application/database secrets elsewhere.
- **Secrets:** CloudFormation `NoEcho` hides parameter values in most stack
  displays, but bootstrap configuration and instance metadata are not a
  production-grade secrets store. Use generated secrets, never commit them, and
  delete the stack after the lab.
- **CloudFront deployment delay:** Distribution creation and changes can take
  several minutes. A newly created stack can be complete before CloudFront is
  globally deployed; wait for the distribution status before testing.
- **Origin address changes:** The merged stack uses an Elastic IP, while the
  sandbox template uses the EC2 public DNS name. If the instance is replaced or
  its public address changes, update/recreate the stack so CloudFront points at
  the new origin.
- **Market data:** Yahoo Finance may rate-limit or block cloud-hosted requests.
  The app labels its demo-data fallback; these figures are illustrative, not
  investment advice.
- **Production gap:** This design is for a temporary educational sandbox, not
  production. A production system should use private compute-to-origin
  connectivity, managed secrets, HTTPS from CloudFront to the origin, stronger
  monitoring, backup/recovery planning, and high availability.

To clean up, delete the CloudFormation stack when finished and wait until the
stack deletion completes. This removes the CloudFront distribution and its
origin resources but retains the non-empty snapshot archive bucket; delete that
bucket separately only after confirming its contents are no longer needed.
