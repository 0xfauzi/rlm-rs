import { DynamoDBClient, ListTablesCommand, PutItemCommand } from "@aws-sdk/client-dynamodb";

import { DDB_TABLE_PREFIX } from "./config";

const DEFAULT_REGION = "us-east-1";

export async function putApiKey(
  hash: string,
  tenantId: string,
  endpoint?: string,
): Promise<void> {
  const client = new DynamoDBClient({
    region: DEFAULT_REGION,
    endpoint,
    credentials: {
      accessKeyId: "test",
      secretAccessKey: "test",
    },
  });

  const key = `KEY#${hash}`;
  const tableName = `${DDB_TABLE_PREFIX}_api_keys`;

  await client.send(
    new PutItemCommand({
      TableName: tableName,
      Item: {
        PK: { S: key },
        SK: { S: key },
        tenant_id: { S: tenantId },
      },
    }),
  );
}

export async function listTables(prefix: string, endpoint?: string): Promise<string[]> {
  const client = new DynamoDBClient({
    region: DEFAULT_REGION,
    endpoint,
    credentials: {
      accessKeyId: "test",
      secretAccessKey: "test",
    },
  });

  const tables: string[] = [];
  let lastEvaluatedTableName: string | undefined;

  do {
    const response = await client.send(
      new ListTablesCommand({
        ExclusiveStartTableName: lastEvaluatedTableName,
      }),
    );
    if (response.TableNames) {
      tables.push(...response.TableNames);
    }
    lastEvaluatedTableName = response.LastEvaluatedTableName;
  } while (lastEvaluatedTableName);

  if (!prefix) {
    return tables;
  }
  return tables.filter((table) => table.startsWith(prefix));
}
