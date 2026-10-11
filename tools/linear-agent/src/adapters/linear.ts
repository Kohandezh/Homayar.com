import type { Issue, LinearPort, WorkflowState } from "../types";

export const LINEAR_ENDPOINT = "https://api.linear.app/graphql";

type FetchFn = typeof fetch;

const ISSUE_FIELDS = `
  id
  identifier
  number
  title
  description
  url
  priority
  createdAt
  team { key }
  state { id name type }
  labels(first: 100) { nodes { name } }
  inverseRelations(first: 100) { nodes { type issue { identifier state { type } } } }
  comments(first: 100) { nodes { body createdAt user { name displayName } botActor { name } } }
`;

const LIST_ISSUES = `
  query LinearAgentIssues($teamKey: String!, $stateName: String!, $labelName: String!, $after: String) {
    issues(
      first: 50
      after: $after
      filter: {
        team: { key: { eq: $teamKey } }
        state: { name: { eq: $stateName } }
        labels: { some: { name: { eq: $labelName } } }
      }
    ) {
      nodes { ${ISSUE_FIELDS} }
      pageInfo { hasNextPage endCursor }
    }
  }
`;

const GET_ISSUE = `
  query LinearAgentIssue($id: String!) {
    issue(id: $id) { ${ISSUE_FIELDS} }
  }
`;

const LIST_STATES = `
  query LinearAgentStates($teamKey: String!) {
    workflowStates(first: 100, filter: { team: { key: { eq: $teamKey } } }) {
      nodes { id name type position }
    }
  }
`;

const FIND_LABELS = `
  query LinearAgentLabels($name: String!) {
    issueLabels(first: 50, filter: { name: { eq: $name } }) {
      nodes { id name team { key } }
    }
  }
`;

const SET_STATE = `
  mutation LinearAgentSetState($id: String!, $stateId: String!) {
    issueUpdate(id: $id, input: { stateId: $stateId }) { success }
  }
`;

const ADD_COMMENT = `
  mutation LinearAgentComment($issueId: String!, $body: String!) {
    commentCreate(input: { issueId: $issueId, body: $body }) { success }
  }
`;

const ADD_LABEL = `
  mutation LinearAgentAddLabel($id: String!, $labelId: String!) {
    issueAddLabel(id: $id, labelId: $labelId) { success }
  }
`;

const REMOVE_LABEL = `
  mutation LinearAgentRemoveLabel($id: String!, $labelId: String!) {
    issueRemoveLabel(id: $id, labelId: $labelId) { success }
  }
`;

const ATTACH_LINK = `
  mutation LinearAgentAttachLink($issueId: String!, $url: String!, $title: String) {
    attachmentLinkURL(issueId: $issueId, url: $url, title: $title) { success }
  }
`;

interface RawIssue {
  id: string;
  identifier: string;
  number: number;
  title: string;
  description: string | null;
  url: string;
  priority: number;
  createdAt: string;
  team: { key: string };
  state: WorkflowState;
  labels: { nodes: { name: string }[] };
  inverseRelations: { nodes: { type: string; issue: { identifier: string; state: { type: string } } }[] };
  comments: {
    nodes: {
      body: string;
      createdAt: string;
      user: { name: string; displayName: string } | null;
      botActor: { name: string | null } | null;
    }[];
  };
}

interface GraphQlResponse<T> {
  data?: T;
  errors?: { message: string }[];
}

export class LinearApiError extends Error {
  override name = "LinearApiError";
}

const MAX_PAGES = 20;

export function toIssue(raw: RawIssue): Issue {
  return {
    id: raw.id,
    identifier: raw.identifier,
    number: raw.number,
    title: raw.title,
    description: raw.description ?? "",
    url: raw.url,
    priority: raw.priority,
    createdAt: raw.createdAt,
    teamKey: raw.team.key,
    state: { id: raw.state.id, name: raw.state.name, type: raw.state.type },
    labels: raw.labels.nodes.map((label) => label.name),
    // A "blocks" relation points from the blocker (issue) to the blocked issue (relatedIssue),
    // so the blockers of this issue are the sources of its inverse relations.
    blockedBy: raw.inverseRelations.nodes
      .filter((relation) => relation.type === "blocks")
      .map((relation) => ({ identifier: relation.issue.identifier, stateType: relation.issue.state.type })),
    comments: raw.comments.nodes
      .map((comment) => ({
        author: comment.user?.displayName || comment.user?.name || comment.botActor?.name || "unknown",
        body: comment.body,
        createdAt: comment.createdAt,
      }))
      .sort((a, b) => a.createdAt.localeCompare(b.createdAt)),
  };
}

/** LinearPort over the GraphQL API. Labels and states are found by name; no id is hard-coded. */
export function createLinearClient(options: {
  apiKey: string;
  teamKey: string;
  endpoint?: string;
  fetchFn?: FetchFn;
}): LinearPort {
  const endpoint = options.endpoint ?? LINEAR_ENDPOINT;
  const fetchFn = options.fetchFn ?? fetch;
  const labelIds = new Map<string, string | null>();

  async function request<T>(query: string, variables: Record<string, unknown>): Promise<T> {
    const response = await fetchFn(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: options.apiKey },
      body: JSON.stringify({ query, variables }),
    });
    const text = await response.text();
    let body: GraphQlResponse<T>;
    try {
      body = JSON.parse(text) as GraphQlResponse<T>;
    } catch {
      throw new LinearApiError(`Linear API returned HTTP ${response.status} with a body that is not JSON.`);
    }
    if (body.errors && body.errors.length > 0) {
      throw new LinearApiError(`Linear API error: ${body.errors.map((e) => e.message).join("; ")}`);
    }
    if (!response.ok || !body.data) throw new LinearApiError(`Linear API returned HTTP ${response.status}.`);
    return body.data;
  }

  async function mutate(query: string, variables: Record<string, unknown>, name: string): Promise<void> {
    const data = await request<Record<string, { success: boolean } | null>>(query, variables);
    if (!data[name]?.success) throw new LinearApiError(`Linear mutation ${name} did not succeed.`);
  }

  /** Prefers a label of the team over a workspace label with the same exact name. */
  async function labelId(name: string): Promise<string | null> {
    if (labelIds.has(name)) return labelIds.get(name) ?? null;
    const data = await request<{ issueLabels: { nodes: { id: string; name: string; team: { key: string } | null }[] } }>(
      FIND_LABELS,
      { name },
    );
    const exact = data.issueLabels.nodes.filter((label) => label.name === name);
    const match =
      exact.find((label) => label.team?.key === options.teamKey) ?? exact.find((label) => label.team === null);
    const id = match?.id ?? null;
    labelIds.set(name, id);
    return id;
  }

  return {
    async listIssues({ teamKey, stateName, labelName }) {
      const issues: Issue[] = [];
      let after: string | null = null;
      for (let page = 0; page < MAX_PAGES; page += 1) {
        const data: { issues: { nodes: RawIssue[]; pageInfo: { hasNextPage: boolean; endCursor: string | null } } } =
          await request(LIST_ISSUES, { teamKey, stateName, labelName, after });
        issues.push(...data.issues.nodes.map(toIssue));
        if (!data.issues.pageInfo.hasNextPage) break;
        after = data.issues.pageInfo.endCursor;
      }
      return issues;
    },

    async getIssue(id) {
      try {
        const data = await request<{ issue: RawIssue | null }>(GET_ISSUE, { id });
        return data.issue ? toIssue(data.issue) : null;
      } catch (error) {
        if (error instanceof LinearApiError && /not found/i.test(error.message)) return null;
        throw error;
      }
    },

    async listStates(teamKey) {
      const data = await request<{ workflowStates: { nodes: (WorkflowState & { position: number })[] } }>(LIST_STATES, {
        teamKey,
      });
      return [...data.workflowStates.nodes]
        .sort((a, b) => a.position - b.position)
        .map(({ id, name, type }) => ({ id, name, type }));
    },

    async setState(issueId, stateId) {
      await mutate(SET_STATE, { id: issueId, stateId }, "issueUpdate");
    },

    async addComment(issueId, body) {
      await mutate(ADD_COMMENT, { issueId, body }, "commentCreate");
    },

    async addLabel(issueId, labelName) {
      const id = await labelId(labelName);
      if (!id) throw new LinearApiError(`Label "${labelName}" not found for team ${options.teamKey}.`);
      await mutate(ADD_LABEL, { id: issueId, labelId: id }, "issueAddLabel");
    },

    async removeLabel(issueId, labelName) {
      const id = await labelId(labelName);
      if (!id) return;
      await mutate(REMOVE_LABEL, { id: issueId, labelId: id }, "issueRemoveLabel");
    },

    async attachLink(issueId, url, title) {
      await mutate(ATTACH_LINK, { issueId, url, title }, "attachmentLinkURL");
    },
  };
}
