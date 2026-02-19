import {
  ASTER_DEX_GUIDANCE,
  ASTER_DEX_ENDPOINTS,
  ASTER_TRADING_WORKFLOWS,
  ASTER_DEFAULTS,
  getEndpointsByGroup,
} from './index';

export function getAsterDexGuidanceExample(): string {
  const allEndpoints = ASTER_DEX_ENDPOINTS
    .map((endpoint) => `- ${endpoint.method} ${endpoint.path}: ${endpoint.description}`)
    .join('\n');

  const workflowSection = ASTER_TRADING_WORKFLOWS
    .map((workflow) => {
      const steps = workflow.steps.map((step, index) => `  ${index + 1}. ${step}`).join('\n');
      return `- ${workflow.name}\n${steps}`;
    })
    .join('\n');

  return [
    '# Aster DEX Guidance',
    ASTER_DEX_GUIDANCE.summary,
    '',
    '## Core notes',
    ...ASTER_DEX_GUIDANCE.notes.map((note) => `- ${note}`),
    '',
    '## Reference endpoints',
    allEndpoints,
    '',
    '## Suggested workflows',
    workflowSection,
    '',
    '## Service defaults',
    `- Health path: ${ASTER_DEFAULTS.healthPath}`,
    `- Base URL env var: ${ASTER_DEFAULTS.baseUrlEnvVar}`,
    `- Service port env var: ${ASTER_DEFAULTS.servicePortEnvVar}`,
  ].join('\n');
}

export function getAsterDexTradingRequestExamples(): string {
  const tradingEndpoints = getEndpointsByGroup('trading');

  return tradingEndpoints
    .filter((endpoint) => endpoint.requestExample)
    .map((endpoint) => {
      const payload = JSON.stringify(endpoint.requestExample, null, 2);
      return [
        `${endpoint.method} ${endpoint.path}`,
        payload,
      ].join('\n');
    })
    .join('\n\n');
}

