import { PutObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { Upload } from "@aws-sdk/lib-storage";

const DEFAULT_REGION = "us-east-1";

export async function uploadFile(
  bucket: string,
  key: string,
  file: File,
  endpoint?: string,
  onProgress?: (percentage: number) => void,
): Promise<void> {
  const client = new S3Client({
    region: DEFAULT_REGION,
    endpoint,
    forcePathStyle: true,
    credentials: {
      accessKeyId: "test",
      secretAccessKey: "test",
    },
  });

  if (!onProgress) {
    await client.send(
      new PutObjectCommand({
        Bucket: bucket,
        Key: key,
        Body: file,
        ContentType: file.type || "application/octet-stream",
      }),
    );
    return;
  }

  const uploader = new Upload({
    client,
    params: {
      Bucket: bucket,
      Key: key,
      Body: file,
      ContentType: file.type || "application/octet-stream",
    },
  });

  uploader.on("httpUploadProgress", (progress) => {
    if (typeof progress.loaded !== "number" || typeof progress.total !== "number") {
      return;
    }
    const percentage = Math.min(100, Math.round((progress.loaded / progress.total) * 100));
    onProgress(percentage);
  });

  await uploader.done();
}
