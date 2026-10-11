function [state_next,lfsr_next,field] = rtl_ising_step(state,J,lfsr,a,b,noise_amp)
% Bit-accurate synchronous RTL update: 1=+1, 0=-1, zero resolves to +1.
  n = numel(state);
  if any(size(J) ~= [n n]), error('J must be N-by-N'); end
  values = [a b noise_amp];
  if any(values ~= fix(values)) || any(values < -32768) || any(values > 32767)
    error('Coefficients/noise amplitude must be signed-int16 integers');
  end
  spins = 2*double(logical(state(:)))-1;
  coupling = int64(J*spins);
  lfsr_next = zeros(size(lfsr),'uint32');
  noise = zeros(n,1,'int64');
  for block = 1:numel(lfsr)
    value = lfsr(block);
    feedback = xor(xor(bitget(value,32),bitget(value,22)), ...
                   xor(bitget(value,2),bitget(value,1)));
    value = bitor(bitshift(value,1),uint32(feedback));
    lfsr_next(block) = value;
    first = (block-1)*32+1;
    signs = 2*double(logical(bitget(value,1:32)).')-1;
    noise(first:first+31) = int64(noise_amp).*int64(signs);
  end
  wide = int64(a).*int64(spins) + int64(b).*coupling + noise;
  two32 = int64(4294967296); two31 = int64(2147483648);
  field = mod(wide,two32);
  negative = field >= two31;
  field(negative) = field(negative)-two32;
  state_next = field >= 0;
end
