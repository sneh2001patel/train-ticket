Generates 200 traces per category
dataset/

- login/
- search/
- booking/
- payment/
- cancel/

Top Suspicios endpoints

- ts-gateway-service
- ts-inside-payment-service
- ts-order-service
- ts-travel-service

What you have achieved

You now have a working loop like this:

1. baseline span analysis found ts-order-service as high-variance

2. you adaptively added finer instrumentation there

3. redeployed only that service

4. generated workload

5. confirmed the fine-grained logs appear

That is the core simplified Pythia idea.

The key claim you can now make
A coarse analysis identified ts-order-service as suspicious.
After adaptively enabling finer instrumentation in that service, the system exposed a hidden performance issue and separated delayed executions from normal ones.

TODO:
Inject anomalies in Train Ticket to create man made anomalies and detect them
Random ones dont work

Find out what the data is
What does Pythia Data and Skywalking Data respresent - What does it record
How does it work?
Why does it work?
When does Pythia start to work compared to Skywalking (normal)?
Where does it work (service)?

What does Pythia actually do (in terms I understand)
