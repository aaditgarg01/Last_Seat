"""HTTP load test. Start with a FRESH database; creates real users and bookings."""
import argparse
import asyncio
import json
import statistics
import time
from collections import Counter
from pathlib import Path
import httpx

async def main(args):
    semaphore = asyncio.Semaphore(args.concurrency)
    timeout = httpx.Timeout(120)
    async with httpx.AsyncClient(base_url=args.url,timeout=timeout,limits=httpx.Limits(max_connections=args.concurrency)) as c:
        async def register(i):
            async with semaphore:
                r = await c.post('/users',json={'name':f'load-{i}'})
                r.raise_for_status()
                return r.json()['token']
        tokens = await asyncio.gather(*(register(i) for i in range(args.users)))
        latencies, codes, winners, errors = [],Counter(),[],[]
        async def attempt(i):
            async with semaphore:
                start = time.perf_counter()
                try:
                    r = await c.post('/reservations',headers={'Authorization':f'Bearer {tokens[i]}','Idempotency-Key':f'load-{i}'},json={'seat_id':1 if args.hotseat else i%500+1})
                    latencies.append((time.perf_counter()-start)*1000)
                    codes[r.status_code]+=1
                    if r.status_code==201:
                        winners.append((i,r.json()['id'],r.json()['seat_id']))
                except httpx.HTTPError as e:
                    errors.append(type(e).__name__)
        started = time.perf_counter()
        await asyncio.gather(*(attempt(i) for i in range(args.users)))
        elapsed = time.perf_counter()-started
        async def confirm(w):
            i,rid,_ = w
            async with semaphore:
                r = await c.post(f'/reservations/{rid}/confirm',headers={'Authorization':f'Bearer {tokens[i]}','Idempotency-Key':f'pay-{i}'},json={'outcome':'success'})
                return r.status_code==200 and r.json()['status']=='confirmed'
        confirmations = await asyncio.gather(*(confirm(w) for w in winners))
        expected = 1 if args.hotseat else min(500,args.users)
        report = {'users':args.users,'concurrency':args.concurrency,'hotseat':args.hotseat,'reservation_seconds':round(elapsed,3),
          'requests_per_second':round(args.users/elapsed,2),'status_codes':dict(codes),'transport_errors':dict(Counter(errors)),
          'latency_ms':{'p50':round(statistics.median(latencies),2),'p95':round(sorted(latencies)[int(len(latencies)*.95)-1],2),'max':round(max(latencies),2)},
          'successful_reservations':len(winners),'unique_winning_seats':len({w[2] for w in winners}),
          'confirmed':sum(confirmations),'expected_winners':expected}
        report['passed'] = (len(winners)==expected==len({w[2] for w in winners})==sum(confirmations)
                            and codes[409]==args.users-expected and not errors and sum(codes.values())==args.users)
        Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
        if not report['passed']:
            raise SystemExit(1)

if __name__=='__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',default='http://localhost:8000')
    p.add_argument('--users',type=int,default=10000)
    p.add_argument('--concurrency',type=int,default=200)
    p.add_argument('--hotseat',action='store_true')
    p.add_argument('--output',default='docs/load-results.json')
    args = p.parse_args()
    if args.users<1 or args.concurrency<1:
        p.error('users and concurrency must be positive')
    asyncio.run(main(args))
