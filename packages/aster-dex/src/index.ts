export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'DELETE';

export type EndpointGroup = 'service' | 'market' | 'account' | 'trading';

export type AsterEndpointReference = {
  path: string;
  method: HttpMethod;
  group: EndpointGroup;
  description: string;
  requiredFields: string[];
  requestExample?: Record<string, unknown>;
};

export type TradingWorkflow = {
  name: string;
  steps: string[];
};

export const ASTER_DEX_GUIDANCE = {
  summary: 'Reference package for Aster DEX perpetual futures integrations in generated Nskills apps.',
  isFunctional: false,
  notes: [
    'This package is guidance-first and does not execute trades by itself.',
    'Use src/aster-service.py for endpoint contracts, dependency flow, and EIP-712 signing patterns.',
    'Keep live signing logic in backend services, never in client-side code.',
    'Add authentication, authorization, rate limits, and audit logs before production usage.',
  ],
} as const;

export const ASTER_DEFAULTS = {
  healthPath: '/health',
  baseUrlEnvVar: 'ASTER_BASE_URL',
  servicePortEnvVar: 'ASTER_SERVICE_PORT',
} as const;

export const ASTER_DEX_ENDPOINTS: AsterEndpointReference[] = [
  {
    path: '/health',
    method: 'GET',
    group: 'service',
    description: 'Health status, network mode, and Aster API reachability.',
    requiredFields: [],
  },
  {
    path: '/symbols',
    method: 'GET',
    group: 'market',
    description: 'List all available tradable symbols and precision metadata.',
    requiredFields: [],
  },
  {
    path: '/market-data',
    method: 'GET',
    group: 'market',
    description: '24h market/ticker data for all symbols or a specific symbol.',
    requiredFields: [],
  },
  {
    path: '/price',
    method: 'GET',
    group: 'market',
    description: 'Current price lookup for a token (resolved to Aster symbol).',
    requiredFields: ['token'],
  },
  {
    path: '/balance',
    method: 'POST',
    group: 'account',
    description: 'Account balance summary using signed user + agent credentials.',
    requiredFields: ['userAddress'],
    requestExample: { userAddress: '0xYourWalletAddress' },
  },
  {
    path: '/positions',
    method: 'POST',
    group: 'account',
    description: 'Open position details (optionally filtered by symbol).',
    requiredFields: ['userAddress'],
    requestExample: { userAddress: '0xYourWalletAddress', symbol: 'BTC' },
  },
  {
    path: '/open-position',
    method: 'POST',
    group: 'trading',
    description: 'Place MARKET/LIMIT order to open long/short perpetual position.',
    requiredFields: ['userAddress', 'symbol', 'side', 'quantity'],
    requestExample: {
      userAddress: '0xYourWalletAddress',
      symbol: 'BTC',
      side: 'long',
      quantity: 0.01,
      leverage: 10,
      type: 'MARKET',
    },
  },
  {
    path: '/close-position',
    method: 'POST',
    group: 'trading',
    description: 'Close full or partial position with reduce-only order.',
    requiredFields: ['userAddress', 'symbol'],
    requestExample: { userAddress: '0xYourWalletAddress', symbol: 'BTC' },
  },
  {
    path: '/set-take-profit',
    method: 'POST',
    group: 'trading',
    description: 'Set TP trigger via stopPrice or percentage model.',
    requiredFields: ['userAddress', 'symbol'],
    requestExample: { userAddress: '0xYourWalletAddress', symbol: 'BTC', takeProfitPercent: 0.3 },
  },
  {
    path: '/set-stop-loss',
    method: 'POST',
    group: 'trading',
    description: 'Set SL trigger via stopPrice or percentage model.',
    requiredFields: ['userAddress', 'symbol'],
    requestExample: { userAddress: '0xYourWalletAddress', symbol: 'BTC', stopLossPercent: 0.1 },
  },
  {
    path: '/change-leverage',
    method: 'POST',
    group: 'trading',
    description: 'Update leverage for a symbol before/after position actions.',
    requiredFields: ['userAddress', 'symbol', 'leverage'],
    requestExample: { userAddress: '0xYourWalletAddress', symbol: 'BTC', leverage: 15 },
  },
  {
    path: '/cancel-order',
    method: 'POST',
    group: 'trading',
    description: 'Cancel pending order by orderId or clientOrderId.',
    requiredFields: ['userAddress', 'symbol'],
    requestExample: { userAddress: '0xYourWalletAddress', symbol: 'BTC', orderId: 12345 },
  },
  {
    path: '/all-orders',
    method: 'POST',
    group: 'trading',
    description: 'Historical order query for a symbol with optional filters.',
    requiredFields: ['userAddress', 'symbol'],
    requestExample: { userAddress: '0xYourWalletAddress', symbol: 'BTC', limit: 50 },
  },
];

export const ASTER_TRADING_WORKFLOWS: TradingWorkflow[] = [
  {
    name: 'Open New Position',
    steps: [
      'Fetch symbols and validate market.',
      'Optionally fetch market-data/price and present context.',
      'Set leverage if user requested a specific value.',
      'Call /open-position with explicit user-approved parameters.',
    ],
  },
  {
    name: 'Risk Management',
    steps: [
      'Fetch current positions to validate side and open size.',
      'Apply /set-take-profit and /set-stop-loss with user-confirmed thresholds.',
      'Return resulting trigger values and caution if immediate trigger risk exists.',
    ],
  },
  {
    name: 'Close and Reconcile',
    steps: [
      'Read current position from /positions.',
      'Call /close-position for full or partial size.',
      'Read /positions and /all-orders to verify final state.',
    ],
  },
];

export function getEndpointsByGroup(group: EndpointGroup): AsterEndpointReference[] {
  return ASTER_DEX_ENDPOINTS.filter((endpoint) => endpoint.group === group);
}

export function getEndpointReference(path: string): AsterEndpointReference | undefined {
  return ASTER_DEX_ENDPOINTS.find((endpoint) => endpoint.path === path);
}

